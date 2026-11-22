"""格式规则引擎：10 条纯函数规则，输出可直接用于前端定位的问题列表。"""
import re
from itertools import islice, pairwise

from config.paper import DEFAULT_FORMAT

_FIG_REF_RE = re.compile(r"图\s*(\d+)(?:[\-–—.](\d+))?")
_TAB_REF_RE = re.compile(r"表\s*(\d+)(?:[\-–—.](\d+))?")
_CAPTION_NUM_RE = re.compile(r"^(图|表)\s*(\d+)", re.IGNORECASE)
_REF_TYPE_RE = re.compile(r"\[\s*(J|M|C|D|N|S|P|R|Z|A|G|EB/OL|DB/OL|J/OL|M/OL)\s*\]", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_CITE_RE = re.compile(r"\[\s*(\d+)\s*\]")
_HALF_PUNCT_RE = re.compile(r"[\u4e00-\u9fff][,.]|[,.]\s*[\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CN_HEADING_RE = re.compile(r"^\s*第([一二三四五六七八九十百]+)[章节篇]")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}

_REF_STYLE_LABELS = {"gbt7714": "GB/T 7714", "apa": "APA", "ieee": "IEEE"}

_SEVERITY_DEDUCT = {"high": 8, "medium": 4, "low": 2}


def _issue(rule_id: str, severity: str, page, paragraph_id: str,
           evidence: str, suggestion: str, basis: str) -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "page": page or 0,
        "paragraph_id": paragraph_id or "",
        "evidence": (evidence or "")[:120],
        "suggestion": suggestion,
        "basis": basis,
    }


def _body_paragraphs(parsed: dict) -> list[dict]:
    return [
        p for p in parsed.get("paragraphs") or []
        if p.get("type") == "body" and not p.get("table_id")
    ]


def _first_body_pid(section: dict, body_ids: set[str]) -> str:
    """定位到章节内首个正文段，避免把标题/目录当作可改写目标。"""
    return next(
        (pid for pid in section.get("paragraph_ids") or [] if pid in body_ids),
        "",
    )


def _cn_number(text: str) -> int | None:
    """解析 1~999 的中文序号（一、十二、二十三、一百零五）。"""
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current or None


def _numbering_level1(sections: list[dict],
                      style: str = "decimal") -> list[tuple[int, dict]]:
    """按所选标题编号样式提取一级章节序号；样式为 none 时不检查连续编号。"""
    if style == "none":
        return []
    out = []
    for s in sections:
        title = s.get("title") or ""
        if int(s.get("level") or 1) != 1:
            continue
        if style == "chinese":
            m = _CN_HEADING_RE.match(title)
            number = _cn_number(m.group(1)) if m else None
        else:
            m = re.match(r"^\s*(\d{1,3})(?:\s|$)", title)
            number = int(m.group(1)) if m else None
        if number:
            out.append((number, s))
    return out


def _language(parsed: dict, spec: dict) -> str:
    """模板优先决定语言；自定义模板时按正文/摘要的中文字符判断。"""
    if spec.get("template") in ("ieee", "apa"):
        return "en"
    sample = " ".join([
        str(parsed.get("abstract") or ""),
        " ".join(str(k) for k in parsed.get("keywords") or []),
        " ".join(str(p.get("text") or "")[:200]
                 for p in (parsed.get("paragraphs") or [])[:10]),
    ])
    return "zh" if _CJK_RE.search(sample) else "en"


def check_format(parsed: dict, format_spec: dict | None = None) -> dict:
    """执行 10 条格式规则并评分（100 起扣：high 8 / medium 4 / low 2）。"""
    spec = dict(DEFAULT_FORMAT)
    spec.update(format_spec or {})
    paragraphs = parsed.get("paragraphs") or []
    sections = parsed.get("sections") or []
    body = _body_paragraphs(parsed)
    body_ids = {p.get("id") for p in body}
    refs = [p for p in paragraphs if p.get("type") == "reference"]
    captions = [p for p in paragraphs if p.get("type") == "caption"]
    keywords = parsed.get("keywords") or []
    abstract = parsed.get("abstract") or ""
    issues: list[dict] = []
    language = _language(parsed, spec)
    ref_style = str(spec.get("reference_style") or "gbt7714").lower()
    numbering = str(spec.get("heading_numbering") or "decimal")

    fig_caps, tab_caps = set(), set()
    for c in captions:
        m = _CAPTION_NUM_RE.match((c.get("text") or "").strip())
        if not m:
            continue
        (fig_caps if m.group(1) == "图" else tab_caps).add(int(m.group(2)))

    # FMT-001 必需章节缺失
    has_abstract = bool(abstract.strip()) or any(
        "摘要" in (s.get("title") or "") or "abstract" in (s.get("title") or "").lower()
        for s in sections
    )
    has_keywords = bool(keywords) or any("关键词" in (p.get("text") or "") for p in paragraphs)
    has_refs = bool(refs) or any("参考文献" in (s.get("title") or "") for s in sections)
    missing = []
    if not has_abstract:
        missing.append("摘要")
    if not has_keywords:
        missing.append("关键词")
    if not has_refs:
        missing.append("参考文献")
    for name in missing:
        issues.append(_issue(
            "FMT-001", "high", 1, "",
            f"未识别到{name}",
            f"补充{name}部分（摘要建议 200~1000 字，关键词 3~8 个）",
            "学位论文 / 期刊论文结构规范",
        ))

    # FMT-002 标题层级跳级
    prev_level = 0
    for s in sections:
        level = int(s.get("level") or 1)
        if prev_level and level > prev_level + 1:
            first_pid = _first_body_pid(s, body_ids)
            issues.append(_issue(
                "FMT-002", "medium", s.get("page"), first_pid,
                f"章节「{s.get('title')}」层级为 {level}，上一节为 {prev_level}",
                "补齐中间层级标题，或调整该节编号",
                "标题层级应逐级递进",
            ))
        prev_level = level

    # FMT-003 章节编号断档
    numbered = _numbering_level1(sections, numbering)
    for (a_num, a_s), (b_num, b_s) in pairwise(numbered):
        if b_num > a_num + 1:
            first_pid = _first_body_pid(b_s, body_ids)
            issues.append(_issue(
                "FMT-003", "medium", b_s.get("page"), first_pid,
                f"章节编号从 {a_num} 跳到 {b_num}",
                f"补齐第 {a_num + 1} 章，或重新连续编号",
                "章节编号应连续",
            ))

    # FMT-004 正文引用图/表但缺少对应题注
    missing_fig, missing_tab = set(), set()
    for p in body:
        text = p.get("text") or ""
        for m in _FIG_REF_RE.finditer(text):
            n = int(m.group(1))
            if n not in fig_caps:
                missing_fig.add(n)
                issues.append(_issue(
                    "FMT-004", "medium", p.get("page"), p.get("id"),
                    f"正文引用「图 {n}」但未找到图题注",
                    f"补充「图 {n}」题注，或修正正文引用编号",
                    "图表题注与正文引用应一一对应",
                ))
        for m in _TAB_REF_RE.finditer(text):
            n = int(m.group(1))
            if n not in tab_caps:
                missing_tab.add(n)
                issues.append(_issue(
                    "FMT-004", "medium", p.get("page"), p.get("id"),
                    f"正文引用「表 {n}」但未找到表题注",
                    f"补充「表 {n}」题注，或修正正文引用编号",
                    "图表题注与正文引用应一一对应",
                ))

    # FMT-004 表题注位置与所选排版选项不一致
    order = {p.get("id"): i for i, p in enumerate(paragraphs)}
    caption_pos = {(p.get("text") or "").strip(): i for i, p in enumerate(paragraphs)
                   if p.get("type") == "caption"}
    caption_above = bool(spec.get("table_caption_above", True))
    for t in parsed.get("tables") or []:
        caption = (t.get("caption") or "").strip()
        anchor = order.get(t.get("paragraph_id") or "")
        cap_pos = caption_pos.get(caption)
        if not caption or anchor is None or cap_pos is None:
            continue
        is_above = cap_pos < anchor
        if is_above != caption_above:
            issues.append(_issue(
                "FMT-004", "low", t.get("page"), t.get("paragraph_id"),
                f"表题注「{caption}」位于表格{'上方' if is_above else '下方'}",
                "表题注应置于表格上方" if caption_above else "表题注应置于表格下方",
                "表题注位置规范",
            ))

    # FMT-005 参考文献条目缺当前样式要求的要素
    # GB/T 7714 要求文献类型标识与年份；APA / IEEE 只需年份，不能因缺 [J] 扣分。
    for p in refs:
        text = p.get("text") or ""
        lacks_type = ref_style == "gbt7714" and not _REF_TYPE_RE.search(text)
        lacks_year = not _YEAR_RE.search(text)
        if lacks_type or lacks_year:
            missing_bits = []
            if lacks_type:
                missing_bits.append("文献类型标识（如 [J]/[M]/[C]/[EB/OL]）")
            if lacks_year:
                missing_bits.append("出版年份")
            issues.append(_issue(
                "FMT-005", "medium", p.get("page"), "",
                text, "补充" + "与".join(missing_bits),
                f"{_REF_STYLE_LABELS.get(ref_style, ref_style)} 参考文献著录规则",
            ))

    # FMT-006 摘要篇幅越界（中文按字、英文按词）
    n_abs = len(re.sub(r"\s", "", abstract))
    n_words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", abstract))
    if language == "zh":
        if n_abs and (n_abs < 200 or n_abs > 1000):
            issues.append(_issue(
                "FMT-006", "medium", 1, "",
                f"摘要约 {n_abs} 字",
                "摘要控制在 200~1000 字（学位论文可适当放宽）",
                "中文摘要篇幅规范",
            ))
    elif n_words and (n_words < 100 or n_words > 400):
        issues.append(_issue(
            "FMT-006", "medium", 1, "",
            f"摘要约 {n_words} 词",
            "摘要控制在 100~400 词（学位论文可适当放宽）",
            "英文摘要篇幅规范",
        ))

    # FMT-007 关键词数量不在 3~8
    if keywords and not (3 <= len(keywords) <= 8):
        issues.append(_issue(
            "FMT-007", "medium", 1, "",
            "关键词：" + "；".join(keywords[:12]),
            "关键词保留 3~8 个，按重要性排序",
            "关键词数量规范",
        ))

    # FMT-008 中文句子混用半角逗号/句号（仅中文论文适用）
    for p in body if language == "zh" else []:
        text = p.get("text") or ""
        if _HALF_PUNCT_RE.search(text):
            m = _HALF_PUNCT_RE.search(text)
            issues.append(_issue(
                "FMT-008", "low", p.get("page"), p.get("id"),
                text[max(0, m.start() - 10): m.end() + 10],
                "中文语句统一使用全角逗号与句号",
                "中文标点使用规范",
            ))

    # FMT-009 段落过长
    for p in body:
        n = len(p.get("text") or "")
        if n > 1000:
            issues.append(_issue(
                "FMT-009", "low", p.get("page"), p.get("id"),
                f"该段 {n} 字",
                "拆分为 2~3 个语义段，每段聚焦一个论点",
                "段落可读性规范",
            ))

    # FMT-010 引用编号不连续或超出参考文献条数
    ref_count = len(refs)
    cited = []
    invalid = []
    upper_text = str(ref_count)
    for p in body:
        for m in _CITE_RE.finditer(p.get("text") or ""):
            number = m.group(1).lstrip("0") or "0"
            # Compare decimal strings before int(): uploaded text can contain
            # arbitrarily long numbers, beyond Python's integer conversion limit.
            if (number == "0" or len(number) > len(upper_text)
                    or len(number) == len(upper_text) and number > upper_text):
                invalid.append((number[:60], p))
            else:
                cited.append((int(number), p))
    if cited or invalid:
        if invalid:
            label, max_para = invalid[0]
            issues.append(_issue(
                "FMT-010", "high", max_para.get("page"), max_para.get("id"),
                f"引用编号 [{label}] 超出参考文献条数或非法（{ref_count} 条）",
                "补充对应参考文献，或修正正文引用编号",
                "引用编号应与参考文献一一对应",
            ))
        nums = {n for n, _ in cited}
        upper = ref_count if invalid else max(nums, default=0)
        gaps = list(islice((n for n in range(1, upper + 1) if n not in nums), 10))
        if gaps:
            first = (invalid or cited)[0][1]
            issues.append(_issue(
                "FMT-010", "medium", first.get("page"), first.get("id"),
                "缺失引用编号：" + "、".join(str(g) for g in gaps[:10]),
                "按出现顺序重新连续编号",
                "引用编号应连续",
            ))

    # 评分
    counts = {"high": 0, "medium": 0, "low": 0}
    for it in issues:
        counts[it["severity"]] += 1
    score = 100 - sum(_SEVERITY_DEDUCT[s] * c for s, c in counts.items())
    score = max(0, score)

    summary = (
        f"格式检查得分 {score} 分，发现 {len(issues)} 个问题"
        f"（高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}）。"
    )
    if not issues:
        summary = "格式检查未发现明显问题。"

    stats = {
        "paragraphs": len(paragraphs),
        "body_paragraphs": len(body),
        "sections": len(sections),
        "references": len(refs),
        "figures": len(fig_caps),
        "tables": len(tab_caps),
        "abstract_chars": n_abs,
        "keywords": len(keywords),
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "template": spec.get("template"),
        "language": language,
        "reference_style": ref_style,
        "heading_numbering": numbering,
        "abstract_words": n_words,
    }
    return {"score": score, "summary": summary, "issues": issues, "stats": stats}
