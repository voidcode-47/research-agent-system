"""论文问答检索索引：章节父块 + 段落子块 + 结构化表格块。

纯关键词召回（中文二元词 + 英文词），不引入向量库；构建是纯函数，
可直接作用于旧版 parsed.json（缺少 tables 时按段落检索），不改动段落、
版本、差异或导出数据。
"""
import re

from paper.similar import _tokens

MAX_PARA_CHARS = 480
TABLE_CHUNK_CHARS = 640
MAX_CHUNKS = 12
MIN_CONTEXT_PIECE = 160
CONTEXT_INTRO = "以下为按相关度排序的论文片段（编号可用于引用）："

_REF_QUERY_RE = re.compile(
    r"参考文献|参考文目|引用|文献|references?|citations?|bibliograph", re.IGNORECASE
)
_REF_SECTION_RE = re.compile(
    r"参考文献|参考文目|references?|bibliography", re.IGNORECASE
)
_SENT_END_RE = re.compile(r"(?<=[。！？；!?;])")
_ABSTRACT_SECTION_RE = re.compile(r"^(?:(?:中文|英文)?摘要|abstract)$", re.IGNORECASE)


def section_paths(sections: list[dict]) -> dict[str, str]:
    """按标题层级生成完整章节路径，如“第二章 / 2.1 方法 / 2.1.1 数据”。"""
    stack: list[tuple[int, str]] = []
    out: dict[str, str] = {}
    for s in sections or []:
        level = int(s.get("level") or 1)
        title = str(s.get("title") or "").strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        out[str(s.get("id") or "")] = " / ".join(t for _, t in stack if t)
    return out


def _split_segments(text: str, limit: int = MAX_PARA_CHARS) -> list[tuple[int, int]]:
    """仅在超长时按句末标点切分，返回原段落中的字符区间。"""
    if len(text) <= limit:
        return [(0, len(text))]
    pieces: list[tuple[int, int]] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        if m.end() > start:
            pieces.append((start, m.end()))
            start = m.end()
    if start < len(text):
        pieces.append((start, len(text)))

    out: list[tuple[int, int]] = []
    cur_start = cur_end = None
    for s, e in pieces:
        while e - s > limit:
            if cur_start is None:
                cur_start = s
            out.append((cur_start, s + limit))
            s = s + limit
            cur_start = s
        if cur_start is None:
            cur_start, cur_end = s, e
        elif e - cur_start <= limit:
            cur_end = e
        else:
            out.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    if cur_start is not None and cur_end > cur_start:
        out.append((cur_start, cur_end))
    return out


def _pid_section_map(sections: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for s in sections or []:
        sid = str(s.get("id") or "")
        for pid in s.get("paragraph_ids") or []:
            mapping[str(pid)] = sid
    return mapping


def _table_chunk_blocks(table: dict, path: str, order: int) -> list[dict]:
    tid = str(table.get("id") or "")
    if not tid:
        return []
    caption = str(table.get("caption") or "").strip()
    page = table.get("page")
    rows = [[str(c or "").strip() for c in row] for row in (table.get("rows") or [])]
    header_rows = int(table.get("header_rows") or 0)
    if rows and header_rows <= 0:
        header_rows = 1
    header = rows[:header_rows]
    body = rows[header_rows:]
    units = str(table.get("units") or "").strip()

    base: list[str] = []
    if caption:
        base.append(f"表题：{caption}")
    if units:
        base.append(f"单位：{units}")
    header_line = "表头：" + " | ".join(header[-1]) if header else ""
    if not rows:
        fallback = str(table.get("fallback_text") or "").strip()
        if not fallback:
            return []
        text = "\n".join(base + [f"（表格结构未提取，保留原文：{fallback}）"])
        return [{
            "kind": "table", "key": f"{tid}#0", "id": tid, "table_id": tid,
            "caption": caption, "page": page, "section_path": path,
            "text": text, "tokens": _tokens(text), "path_tokens": _tokens(path),
            "is_reference": False, "order": order,
        }]

    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for row in body or rows:
        line = " | ".join(row)
        if current and size + len(line) > TABLE_CHUNK_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        groups.append(current)
    if not groups:
        groups = [["（仅表头，无数据行）"]]

    blocks = []
    for i, group in enumerate(groups):
        lines = list(base)
        if header_line:
            lines.append(header_line)
        lines.extend(f"第 {j + 1} 行：{line}" for j, line in enumerate(group))
        text = "\n".join(lines)
        blocks.append({
            "kind": "table", "key": f"{tid}#{i}", "id": tid, "table_id": tid,
            "caption": caption, "page": page, "section_path": path,
            "text": text, "tokens": _tokens(text), "path_tokens": _tokens(path),
            "is_reference": False, "order": order + i,
        })
    return blocks


def build(parsed: dict, paragraphs: list[dict] | None = None) -> dict:
    """构建检索索引；旧数据缺少 tables/章节映射时自动降级。"""
    paragraphs = list(paragraphs if paragraphs is not None else parsed.get("paragraphs") or [])
    sections = parsed.get("sections") or []
    paths = section_paths(sections)
    pid_section = _pid_section_map(sections)
    title_by_section = {str(s.get("id") or ""): str(s.get("title") or "") for s in sections}
    paper_title = str(parsed.get("title") or "").strip()
    paper_abstract = str(parsed.get("abstract") or "").strip()

    para_chunks: list[dict] = []
    order = 0
    for p in paragraphs:
        ptype = p.get("type")
        if ptype not in ("body", "reference"):
            continue
        text = str(p.get("text") or "")
        if not text.strip() or p.get("table_id"):
            continue
        sid = pid_section.get(str(p.get("id"))) or ""
        path = paths.get(sid) or str(p.get("section") or "")
        if not path and paper_title and text.strip() == paper_title:
            continue  # 标题已在概览中给出，不重复占检索预算
        section_title = title_by_section.get(sid) or str(p.get("section") or "")
        if (
            paper_abstract
            and _ABSTRACT_SECTION_RE.match(section_title.strip())
        ):
            continue  # 摘要已在开头概览中呈现，避免重复占预算
        is_ref = ptype == "reference" or bool(_REF_SECTION_RE.search(path or section_title))
        spans = _split_segments(text)
        for i, (s, e) in enumerate(spans):
            piece = text[s:e].strip()
            if not piece:
                continue
            para_chunks.append({
                "kind": "paragraph", "key": f"{p.get('id')}#{i}", "id": str(p.get("id")),
                "segment": i, "segments": len(spans), "start": s, "end": e,
                "text": piece, "page": p.get("page"),
                "section_id": sid, "section_path": path,
                "tokens": _tokens(piece), "path_tokens": _tokens(path or section_title),
                "is_reference": is_ref, "order": order,
            })
            order += 1

    table_chunks: list[dict] = []
    for t in parsed.get("tables") or []:
        if not isinstance(t, dict):
            continue
        path = paths.get(str(t.get("section_id") or "")) or str(t.get("section") or "")
        table_chunks.extend(_table_chunk_blocks(t, path, order))
        order += 1

    parents = []
    for i, s in enumerate(sections):
        sid = str(s.get("id") or "")
        level = int(s.get("level") or 1)
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        parents.append({
            "id": sid,
            "level": level,
            "path": paths.get(sid, ""),
            "page": s.get("page"),
            "leaf": not nxt or int(nxt.get("level") or 1) <= level,
            "paragraph_count": len(s.get("paragraph_ids") or []),
        })

    return {
        "paragraphs": para_chunks,
        "tables": table_chunks,
        "sections": parents,
        "meta": {
            "title": parsed.get("title") or "",
            "abstract": parsed.get("abstract") or "",
            "keywords": parsed.get("keywords") or [],
        },
    }


def _overview_lines(index: dict, budget: int) -> list[str]:
    meta = index.get("meta") or {}
    lines = [f"论文标题：{meta.get('title') or '（未识别）'}"]
    abstract = str(meta.get("abstract") or "").strip()
    if abstract:
        lines.append("摘要：" + abstract[: min(400, max(120, budget // 4))])
    sections = []
    seen: set[str] = set()
    for s in index.get("sections") or []:
        path = s.get("path") or ""
        if path and path not in seen and s.get("leaf", True):
            seen.add(path)
            sections.append(path)
    if sections:
        lines.append("章节结构：" + " / ".join(sections[:15]))
    return lines


def _score(tokens: set[str], path_tokens: set[str], query: set[str]) -> int:
    if not query:
        return 0
    return 2 * len(tokens & query) + len(path_tokens & query)


def _render(chunk: dict, neighbor: bool = False) -> str:
    page = chunk.get("page")
    where = f"第{page}页" if page else "页码未知"
    path = chunk.get("section_path") or "未分章"
    if chunk.get("kind") == "table":
        caption = chunk.get("caption") or "表格"
        head = f"[{chunk['id']} · {caption} · {where} · {path}]"
    else:
        seg = ""
        if int(chunk.get("segments") or 1) > 1:
            seg = f"·片段{int(chunk.get('segment') or 0) + 1}"
        head = f"[{chunk['id']}{seg} · {where} · {path}{' · 相邻段落' if neighbor else ''}]"
    return head + "\n" + chunk.get("text", "")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 8)]
    idx = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("！"), cut.rfind("？"))
    if idx >= MIN_CONTEXT_PIECE // 2:
        cut = cut[: idx + 1]
    return cut.rstrip() + " …（已截断）"


def search(index: dict, question: str, max_chars: int = 3600,
           max_chunks: int = MAX_CHUNKS) -> tuple[str, list[dict]]:
    """按相关度挑选段落/表格块，严格控制在 max_chars 预算内。"""
    budget = max(400, int(max_chars or 3600))
    query = _tokens(question or "")
    want_refs = bool(_REF_QUERY_RE.search(question or ""))

    overview = _overview_lines(index, budget)
    overview_text = "\n".join(overview)
    cap = budget - MIN_CONTEXT_PIECE
    if cap > 60 and len(overview_text) + 1 > cap:
        overview = [_truncate(overview_text, cap - 1)]
        overview_text = overview[0]
    used = min(len(overview_text) + 1, budget)
    if used + len(CONTEXT_INTRO) + 1 < budget:
        used += len(CONTEXT_INTRO) + 1  # 预留片段导语，避免正文挤爆预算
    remaining = budget - used

    candidates = [c for c in index.get("paragraphs") or [] if want_refs or not c.get("is_reference")]
    candidates += list(index.get("tables") or [])
    ranked = sorted(
        candidates,
        key=lambda c: (-_score(c["tokens"], c.get("path_tokens") or set(), query),
                       c.get("page") or 0, c.get("order") or 0),
    )

    chosen: list[dict] = []
    chosen_keys: set[str] = set()
    steps: list[tuple[dict, bool]] = []
    deferred: list[dict] = []
    no_hit = bool(query)
    top_score = 0
    if ranked and query:
        top_score = _score(ranked[0]["tokens"], ranked[0].get("path_tokens") or set(), query)
    floor = max(1, top_score // 4) if query else 1

    for chunk in ranked:
        if len(chosen) >= max_chunks or remaining <= 0:
            break
        score = _score(chunk["tokens"], chunk.get("path_tokens") or set(), query)
        if score <= 0:
            break
        no_hit = False
        if score < floor:
            break  # 排序后分数只会更低，避免弱相关段落占满预算
        block = _render(chunk)
        if len(block) + 1 <= remaining:
            steps.append((chunk, False))
            chosen.append(chunk)
            chosen_keys.add(chunk["key"])
            remaining -= len(block) + 1
        elif remaining >= MIN_CONTEXT_PIECE:
            # 不立即截断占满预算，先记下，继续找后面更短的相关块
            deferred.append(chunk)

    for chunk in deferred:
        if remaining < MIN_CONTEXT_PIECE or len(chosen) >= max_chunks:
            break
        if chunk["key"] in chosen_keys:
            continue
        steps.append((chunk, False))
        chosen.append(chunk)
        chosen_keys.add(chunk["key"])
        remaining = 0

    para_chunks = index.get("paragraphs") or []
    position = {c["key"]: i for i, c in enumerate(para_chunks)}
    for chunk, _ in list(steps):
        if chunk.get("kind") != "paragraph" or remaining < MIN_CONTEXT_PIECE:
            continue
        idx = position.get(chunk["key"])
        if idx is None:
            continue
        for delta in (-1, 1):
            j = idx + delta
            if not (0 <= j < len(para_chunks)):
                continue
            neighbor = para_chunks[j]
            if neighbor["key"] in chosen_keys or neighbor.get("is_reference") and not want_refs:
                continue
            if neighbor.get("id") == chunk.get("id") and neighbor.get("section_id") == chunk.get("section_id"):
                continue
            if neighbor.get("section_id") != chunk.get("section_id"):
                continue
            block = _render(neighbor, neighbor=True)
            if len(block) + 1 <= remaining:
                steps.append((neighbor, True))
                chosen_keys.add(neighbor["key"])
                remaining -= len(block) + 1

    body: list[str] = []
    evidence: list[dict] = []
    seen_ids: set[str] = set()
    spent = used
    for chunk, neighbor in steps:
        block = _render(chunk, neighbor=neighbor)
        room = budget - spent
        if room <= 0:
            break
        if len(block) + 1 > room:
            if room < MIN_CONTEXT_PIECE:
                continue
            block = _truncate(block, room)
        body.append(block)
        spent += len(block) + 1
        key = (chunk.get("kind"), chunk.get("id"))
        if chunk.get("id") and key not in seen_ids:
            seen_ids.add(key)
            item = {
                "kind": chunk["kind"],
                "id": chunk["id"],
                "page": chunk.get("page"),
                "section_path": chunk.get("section_path") or "",
            }
            if chunk["kind"] == "table":
                item["caption"] = chunk.get("caption") or ""
            else:
                item["segment"] = int(chunk.get("segment") or 0)
            evidence.append(item)

    lines = list(overview)
    if body:
        lines.append(CONTEXT_INTRO)
        lines.extend(body)
    elif no_hit:
        lines.append("（未检索到与问题直接相关的段落或表格。）")
    return "\n".join(lines), evidence


def build_qa_context(parsed: dict, question: str, max_chars: int = 3600,
                     paragraphs: list[dict] | None = None) -> tuple[str, list[dict]]:
    """问答入口：构建索引并检索，返回 (上下文, 依据列表)。"""
    return search(build(parsed, paragraphs), question, max_chars)
