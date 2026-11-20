"""PDF / Word 校验与结构解析（PyMuPDF / python-docx）。

识别章节标题、图表题注、参考文献条目，并按句子/长度切分段落。
"""
import math
import os
import re
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

MAX_FILE_MB = 50
MAX_PAGES = 200
MIN_TEXT_CHARS = 100

_PDF_HEAD = b"%PDF-"

# 纯页码 / 页眉噪声行
_NOISE_RE = re.compile(r"^(第\s*\d+\s*页|\d{1,4}|-\s*\d+\s*-|page\s+\d+.*)$", re.IGNORECASE)

# 编号标题：1 绪论 / 1.1 方法 / 2.3.1 实验设置
_NUM_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+(\S.{0,48})$")
# 编号与标题之间没有空格：3.2CNN 模型构建模块 / 2.1需求分析
_NUM_HEADING_NOSPACE_RE = re.compile(
    r"^(\d{1,2}(?:\.\d{1,2}){1,3})\s*([A-Za-z\u4e00-\u9fff].{2,48})$"
)
_CN_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+章[\s、.．:：]*(.*)$")

_UNNUMBERED_HEADINGS = {
    "摘要", "abstract", "引言", "绪论", "前言", "结论", "总结与展望",
    "结论与展望", "研究背景", "参考文献", "references", "致谢",
    "acknowledgements", "acknowledgments", "附录", "appendix",
}

_CAPTION_RE = re.compile(r"^(图|表)\s*\d+([\-–—.]\d+)?|^(fig(?:ure)?\.?|table)\s*\d+", re.IGNORECASE)
_REF_ITEM_RE = re.compile(r"^\[\s*\d+\s*\]")
_KEYWORDS_RE = re.compile(r"(?:关\s*键\s*词|关键字|key\s*words?|keywords)\s*[:：]\s*(.+)", re.IGNORECASE)

# 目录标题 / 目录条目（点线或编号 + 页码）。目录与封面只保留原样，不参与改写。
_TOC_TITLE_RE = re.compile(r"^(?:目\s*录|contents?|table\s+of\s+contents)\s*[:：]?$", re.IGNORECASE)
_TOC_STYLE_RE = re.compile(r"^(?:toc|目录)", re.IGNORECASE)
_TOC_ENTRY_RE = re.compile(
    r"^(?=.{2,140}$).*(?:\.{2,}|…{2,}|·{2,}|_{2,}|-{2,})\s*"
    r"(?:\d{1,4}|[ivxlcdm]{1,6})\s*$",
    re.IGNORECASE,
)
_TOC_ENTRY_PAGE_RE = re.compile(
    r"^\s*(?:(?:第[一二三四五六七八九十百零〇\d]+[章节])|"
    r"(?:\d{1,2}(?:\.\d{1,2}){0,3})|"
    r"(?:[一二三四五六七八九十]{1,3}[、.．]))\s*\S.{0,100}?\s+"
    r"(?:\d{1,4}|[ivxlcdm]{1,6})\s*$",
    re.IGNORECASE,
)
# 目录点线（页码可能被拆成独立文本块，因此页码可有可无）
_TOC_LEADER_RE = re.compile(
    r"(?:\.{3,}|…{2,}|·{3,}|_{3,})\s*(?:\d{1,4}|[ivxlcdm]{1,6})?\s*$",
    re.IGNORECASE,
)
_COVER_STOP_RE = re.compile(
    r"(摘\s*要|abstract|关\s*键\s*词|key\s*words?|引\s*言|绪\s*论)",
    re.IGNORECASE,
)
_COVER_FIELD_RE = re.compile(
    r"^(?:题\s*目|课\s*题|班\s*级|姓\s*名|学\s*号|专\s*业|学\s*院|系\s*别|"
    r"指导教师|指导老师|完成时间|提交日期|日\s*期|课程名称)\s*[:：]"
)
_ABSTRACT_LABEL_RE = re.compile(
    r"^(?:abstract|摘\s*要|关\s*键\s*词|key\s*words?)\s*[:：]",
    re.IGNORECASE,
)
_PDF_PARA_MAX_CHARS = 1200
# 首行缩进识别：正文左边界与段落首行至少相差的磅值
_PDF_INDENT_MIN = 10.0
_NUMBERED_START_RE = re.compile(
    r"^\d{1,2}(?:[.、．]\s*|\s+)|^[一二三四五六七八九十]{1,3}[、.．]"
)
_LIST_ITEM_RE = re.compile(
    r"^\s*(?:"
    r"[（(]\s*(?:\d{1,3}|[一二三四五六七八九十百]{1,4}|[a-zA-Z])\s*[）)]"
    r"|[①-⑳]"
    r"|\d{1,3}\s*[.、)）](?!\d)"
    r"|[一二三四五六七八九十]{1,3}\s*[、.．]"
    r"|[•·▪◦‣]\s+"
    r"|[-*]\s+"
    r")"
)
_LIST_MAX_CHARS = 800


class ParseCancelled(Exception):
    """用户取消了解析（API 任务标记为 cancelled 并清理半成品）。"""


def _report(progress, percent: int, stage: str) -> None:
    if progress:
        progress(max(0, min(100, int(percent))), stage)


def _check_cancel(should_cancel) -> None:
    if should_cancel and should_cancel():
        raise ParseCancelled("解析已取消")


class UploadValidationError(ValueError):
    """上传文件不符合格式约束（API 层转为 415）。"""


class PDFValidationError(UploadValidationError):
    """上传文件不符合 PDF 约束。"""


def validate_pdf(filename: str, head_bytes: bytes,
                 content_type: Optional[str] = None) -> None:
    """校验扩展名 .pdf、文件头 %PDF-、Content-Type；任一不符抛 PDFValidationError。"""
    name = (filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    if ext != ".pdf":
        raise PDFValidationError(f"仅支持 PDF 文件，当前文件类型为 {ext or '未知'}（请导出为 PDF 后上传）")
    if not (head_bytes or b"").startswith(_PDF_HEAD):
        raise PDFValidationError("文件内容不是有效的 PDF（文件头缺少 %PDF- 标识）")
    if content_type and "pdf" not in content_type.lower():
        raise PDFValidationError(f"Content-Type 不是 PDF（当前为 {content_type}）")


def validate_upload(filename: str, head_bytes: bytes,
                    content_type: Optional[str] = None) -> str:
    """校验上传文件并返回类型 "pdf" / "docx"；不符抛 UploadValidationError。"""
    name = (filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    if ext == ".doc":
        raise UploadValidationError("不支持旧版 .doc 文件，请在 Word 中另存为 .docx 或导出 PDF 后上传")
    if ext == ".pdf":
        validate_pdf(name, head_bytes, content_type)
        return "pdf"
    if ext == ".docx":
        if not (head_bytes or b"").startswith(b"PK\x03\x04"):
            raise UploadValidationError("文件内容不是有效的 Word 文档（缺少 ZIP 文件头）")
        ctype = (content_type or "").lower()
        if ctype and not any(x in ctype for x in ("wordprocessingml", "octet-stream", "zip")):
            raise UploadValidationError(f"Content-Type 不是 Word 文档（当前为 {content_type}）")
        return "docx"
    raise UploadValidationError(
        f"仅支持 PDF（.pdf）或 Word（.docx）文件，当前文件类型为 {ext or '未知'}"
    )


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _join_lines(lines: list[str]) -> str:
    """两侧都是中文直接相连，否则补空格。"""
    if not lines:
        return ""
    out = lines[0].strip()
    for nxt in lines[1:]:
        nxt = nxt.strip()
        if not nxt:
            continue
        if out and _is_cjk(out[-1]) and _is_cjk(nxt[0]):
            out += nxt
        else:
            out = out + " " + nxt
    return out.strip()


def _detect_heading(line: str) -> Optional[tuple[int, str]]:
    """返回 (level, title) 或 None。"""
    text = line.strip()
    if not text or len(text) > 60:
        return None
    if text[-1] in "。！？；":
        return None
    low = text.rstrip(" :：").lower()
    compact = re.sub(r"\s+", "", low)
    if low in _UNNUMBERED_HEADINGS or compact in _UNNUMBERED_HEADINGS:
        return 1, text.rstrip(" :：")

    m = _CN_CHAPTER_RE.match(text)
    if m:
        return 1, text.rstrip(" :：")

    for pattern in (_NUM_HEADING_RE, _NUM_HEADING_NOSPACE_RE):
        m = pattern.match(text)
        if not m:
            continue
        num, rest = m.group(1), m.group(2).strip()
        if not rest or len(rest) > 48:
            continue
        if "，" in rest or "," in rest:
            continue
        if rest[0] in "年月日%％":
            continue
        if pattern is _NUM_HEADING_NOSPACE_RE and len(rest) < 3:
            continue
        level = min(num.count(".") + 1, 4)
        return level, f"{num} {rest}"
    return None


def _is_caption_line(text: str) -> bool:
    t = (text or "").strip()
    if not _CAPTION_RE.match(t):
        return False
    # “表2，全模型共…参数。”是正文引用，不是表题
    if re.match(r"^(?:表|table)\s*\d+\s*[，,]", t, re.IGNORECASE) and _ends_sentence(t):
        return False
    return True


def _is_toc_entry(text: str) -> bool:
    """目录条目：点线页码，或“1 绪论 1”这类编号 + 页码。"""
    t = (text or "").strip()
    if not t or len(t) > 160 or t[-1] in "。！？；":
        return False
    return bool(
        _TOC_ENTRY_RE.match(t)
        or _TOC_ENTRY_PAGE_RE.match(t)
        or _TOC_LEADER_RE.search(t)
    )


def _toc_start_index(lines: list[tuple[str, object]]) -> Optional[int]:
    """返回目录区域起始行；同一页可能同时有封面和目录。"""
    leader_rows = [
        i for i, (text, _) in enumerate(lines)
        if text and _TOC_LEADER_RE.search(text)
    ]
    if len(leader_rows) < 3:
        return None
    start = leader_rows[0]
    for j in range(max(0, start - 4), start):
        text = (lines[j][0] or "").strip()
        if not text:
            continue
        if (_TOC_TITLE_RE.match(text) or text in ("目", "录")
                or (_TOC_STYLE_RE.match(text) and len(text) <= 30)):
            start = min(start, j)
    return start


def _is_cover_line(text: str, seen: int) -> bool:
    """封面行：标题/作者/日期等短行，遇到正文句子或摘要立即停止。"""
    t = (text or "").strip()
    if not t or seen >= 30 or len(t) > 100:
        return False
    if _COVER_STOP_RE.search(t) or _TOC_TITLE_RE.match(t):
        return False
    if _NUMBERED_START_RE.match(t):
        return False
    if t[-1] in "。！？；.!?;":
        return False
    if _CAPTION_RE.match(t) or _REF_ITEM_RE.match(t):
        return False
    if _COVER_FIELD_RE.match(t):
        return True
    return True


def _is_cover_heading(text: str, metadata_title: str, index: int) -> bool:
    """Word 中标题可能被套成 Heading 1；只把首页标题识别为封面。"""
    t = (text or "").strip()
    if not t or len(t) > 100 or index > 2:
        return False
    low = t.rstrip(" :：").lower()
    compact = re.sub(r"\s+", "", low)
    if (low in _UNNUMBERED_HEADINGS or compact in _UNNUMBERED_HEADINGS
            or _NUM_HEADING_RE.match(t) or _CN_CHAPTER_RE.match(t)):
        return False
    if _NUMBERED_START_RE.match(t):
        return False
    mt = (metadata_title or "").strip()
    return bool(mt and t == mt) or (index == 1 and not t.endswith(("。", "！", "？")))


def _is_list_paragraph(p: dict) -> bool:
    return (
        p.get("type") == "body"
        and not p.get("table_id")
        and (bool(p.get("list")) or bool(_LIST_ITEM_RE.match(p.get("text") or "")))
    )


def _group_list_paragraphs(paragraphs: list[dict], sections: list[dict]) -> list[dict]:
    """把同一节内连续的列表条目合成一个逻辑段落，内部换行保留条目顺序。"""
    out: list[dict] = []
    removed: set[str] = set()
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        if not _is_list_paragraph(p):
            out.append(p)
            i += 1
            continue
        group = [p]
        total = len((p.get("text") or "").strip())
        j = i + 1
        while j < len(paragraphs):
            q = paragraphs[j]
            if not _is_list_paragraph(q):
                break
            if (q.get("section") or "") != (p.get("section") or ""):
                break
            if q.get("level") != p.get("level"):
                break
            qtext = (q.get("text") or "").strip()
            if total + 1 + len(qtext) > _LIST_MAX_CHARS:
                break
            group.append(q)
            total += 1 + len(qtext)
            j += 1
        if len(group) == 1:
            out.append(p)
            i += 1
            continue
        merged = dict(p)
        merged["text"] = "\n".join((x.get("text") or "").strip() for x in group)
        merged["list_items"] = len(group)
        merged["merged_ids"] = [x.get("id") for x in group]
        if group[-1].get("page") != p.get("page"):
            merged["end_page"] = group[-1].get("page")
        out.append(merged)
        removed.update(x.get("id") for x in group[1:] if x.get("id"))
        i = j
    if removed:
        for sec in sections:
            sec["paragraph_ids"] = [
                pid for pid in (sec.get("paragraph_ids") or []) if pid not in removed
            ]
    return out


def _guess_title(metadata_title: str, paragraphs: list[dict], filename: str) -> str:
    mt = (metadata_title or "").strip()
    if len(mt) >= 4 and mt.lower() not in ("untitled", "无标题", "title"):
        return mt[:200]

    cover = [p for p in paragraphs if p.get("type") == "cover"]
    cover_lines = [(p.get("text") or "").strip() for p in cover]
    cover_text = ""
    for line in cover_lines:
        if line:
            cover_text = _join_lines([cover_text, line]) if cover_text else line
    m = re.search(
        r"题\s*目\s*[:：]\s*(.{4,100}?)"
        r"(?=班\s*级\s*[:：]|姓\s*名\s*[:：]|学\s*号|专\s*业|学\s*院|"
        r"指导教师|指导老师|完成时间|提交日期|日\s*期|$)",
        cover_text,
    )
    if m:
        title = m.group(1).strip(" ：:.-—_")
        if title:
            return title[:200]

    generic = {"摘要", "abstract", "引言", "绪论", "目录", "参考文献", "致谢"}
    school = ("学院", "大学", "学校", "中学")
    candidates = [
        line for line in cover_lines
        if 4 <= len(line) <= 120
        and not _COVER_FIELD_RE.match(line)
        and not re.search(r"\d{4}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日", line)
        and not line.endswith(school)
        and line.rstrip(" :：").lower() not in generic
    ]
    if candidates:
        return max(candidates, key=len)[:200]

    for p in paragraphs[:12]:
        t = (p.get("text") or "").strip()
        if (4 <= len(t) <= 120 and p.get("type") not in ("reference", "table", "caption")
                and t.rstrip(" :：").lower() not in generic
                and "摘要" not in t and not t.endswith(school)):
            return t
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return stem or "未命名论文"


def _extract_keywords(paragraphs: list[dict]) -> list[str]:
    for p in paragraphs:
        m = _KEYWORDS_RE.search(p.get("text") or "")
        if not m:
            continue
        raw = m.group(1)
        items = [x.strip(" .。;；,，、:：") for x in re.split(r"[;；,，、\s]+", raw) if x.strip()]
        out, seen = [], set()
        for it in items:
            key = it.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= 12:
                break
        if out:
            return out
    return []


def _extract_abstract(sections: list[dict], paragraphs: list[dict], full_text: str) -> str:
    para_map = {p["id"]: p for p in paragraphs}
    for s in sections:
        title = (s.get("title") or "").lower().replace(" ", "")
        if title in ("摘要", "abstract") or title.startswith("摘要"):
            parts = [
                para_map[pid]["text"]
                for pid in s.get("paragraph_ids", [])
                if pid in para_map and para_map[pid].get("type") == "body"
            ]
            text = "\n".join(parts).strip()
            if text:
                return text[:800]
    # 兜底：从全文按"摘要 … 关键词/引言"截取
    m = re.search(
        r"(?:摘\s*要|abstract)\s*[:：]?\s*(.{50,1200}?)(?=\n\s*(?:关\s*键\s*词|关键字|key\s*words?)|\n\s*(?:1\s+|引言|绪论))",
        full_text, re.IGNORECASE | re.DOTALL,
    )
    return re.sub(r"\s+", " ", m.group(1)).strip()[:800] if m else ""


_TABLE_CAPTION_RE = re.compile(r"^(?:表|table)\s*[\d一二三四五六七八九十]+", re.IGNORECASE)
_TABLE_NOTE_RE = re.compile(
    r"^(?:注\s*\d*\s*[:：]|注\s+\S|说明\s*[:：]|资料来源|数据来源|表格说明|"
    r"note\s*[:：]|source\s*[:：])",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"[（(]\s*(?:单位|unit)\s*[:：]?\s*([^）)]{1,20})[）)]",
    re.IGNORECASE,
)
_UNIT_TOKEN_RE = re.compile(
    r"[（(]\s*(%|％|ms|s|秒|分钟|小时|次|元|万元|亿元|GB|MB|KB|mm|cm|m|kg|g)\s*[）)]",
    re.IGNORECASE,
)


def _trim_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """补齐为矩形并去掉整行/整列全空的尾部落差；保留内部空单元格与重复值。"""
    out = [list(r) for r in rows]
    while out and not any((c or "").strip() for c in out[-1]):
        out.pop()
    if not out:
        return []
    width = max(len(r) for r in out)
    for r in out:
        if len(r) < width:
            r.extend([""] * (width - len(r)))
        else:
            del r[width:]
    while width and all(not (r[width - 1] or "").strip() for r in out):
        for r in out:
            r.pop()
        width -= 1
    return out


def _table_units(caption: str, header_row: list[str]) -> str:
    """从表题或表头括号标注中提取单位，不做任何推断。"""
    text = " ".join([caption or ""] + [str(c or "") for c in (header_row or [])])
    m = _UNIT_RE.search(text)
    if m:
        return m.group(1).strip()
    units: list[str] = []
    for u in _UNIT_TOKEN_RE.findall(text):
        if u not in units:
            units.append(u)
    return "、".join(units[:3])


def _attach_table_meta(paragraphs: list[dict], tables: list[dict]) -> None:
    """按文档顺序关联表题、单位与脚注；不臆造表题。"""
    if not tables:
        return
    index_by_id = {p.get("id"): i for i, p in enumerate(paragraphs)}
    caption_candidates = [
        (i, p) for i, p in enumerate(paragraphs)
        if p.get("type") == "caption"
        and _TABLE_CAPTION_RE.match((p.get("text") or "").strip())
    ]
    used: set[int] = set()
    for t in tables:
        anchor = index_by_id.get(t.get("paragraph_id") or "")
        best = None
        for i, p in caption_candidates:
            if i in used or p.get("page") != t.get("page"):
                continue
            dist = abs(i - anchor) if anchor is not None else 99
            if dist > 12:
                continue
            if best is None or dist < best[0]:
                best = (dist, i, p)
        if best is not None:
            t["caption"] = (best[2].get("text") or "").strip()
            used.add(best[1])

        rows = t.get("rows") or []
        t["units"] = t.get("units") or _table_units(
            t.get("caption") or "", rows[0] if rows else []
        )
        if rows and not t.get("header_rows"):
            t["header_rows"] = 1
        if rows:
            t["header_source"] = t.get("header_source") or "first-row"

        notes: list[str] = []
        if anchor is not None:
            for j in range(anchor + 1, min(anchor + 4, len(paragraphs))):
                p = paragraphs[j]
                if p.get("type") != "body":
                    break
                text = (p.get("text") or "").strip()
                if text and len(text) <= 300 and _TABLE_NOTE_RE.match(text):
                    notes.append(text)
                    p["table_id"] = t["id"]
                    p["type"] = "table_note"
                else:
                    break
            if not notes:
                # PDF 的表格锚点在该页正文之后生成，表注常出现在锚点之前
                back: list[str] = []
                for j in range(anchor - 1, max(-1, anchor - 5), -1):
                    p = paragraphs[j]
                    if p.get("type") != "body" or p.get("page") != t.get("page"):
                        continue
                    if p.get("table_id"):
                        break
                    text = (p.get("text") or "").strip()
                    if text and len(text) <= 300 and _TABLE_NOTE_RE.match(text):
                        back.append(text)
                        p["table_id"] = t["id"]
                        p["type"] = "table_note"
                        break
                notes = list(reversed(back))
        t["footnotes"] = notes


_SPLIT_LABEL_RE = re.compile(r"^(?:图|表|Figure|Table)\s*\d{1,3}$", re.IGNORECASE)


def _merge_same_row(lines: list[tuple[str, object]], rect_cls) -> list[tuple[str, object]]:
    """合并被拆成多个文本块的同一行：封面“题”+“目：…”、题注“图2”+题名。"""
    merged: list[tuple[str, object]] = []
    for text, rect in lines:
        prev_text, prev_rect = merged[-1] if merged else ("", None)
        if (text and prev_text and rect is not None and prev_rect is not None
                and abs(rect.y0 - prev_rect.y0) <= 3.0
                and prev_rect.x1 <= rect.x0 + 0.5
                and rect.x0 - prev_rect.x1 <= 45
                and (len(prev_text) == 1 or _SPLIT_LABEL_RE.match(prev_text))):
            sep = "" if len(prev_text) == 1 else " "
            merged[-1] = (
                prev_text + sep + text,
                rect_cls(prev_rect.x0, min(prev_rect.y0, rect.y0),
                         max(prev_rect.x1, rect.x1), max(prev_rect.y1, rect.y1)),
            )
            continue
        merged.append((text, rect))
    return merged


def _pdf_page_lines(fitz, page, page_no: int) -> list[tuple[str, object]]:
    """按页面阅读顺序返回 (文本行, 边界框)；无坐标能力时回退纯文本行。"""
    try:
        data = page.get_text("dict")
        out: list[tuple[str, object]] = []
        prev_bottom = None
        for block in data.get("blocks") or []:
            if block.get("type") not in (0, None):
                continue
            for line in block.get("lines") or []:
                text = "".join(
                    str(span.get("text") or "") for span in line.get("spans") or []
                ).strip()
                if not text:
                    continue
                rect = fitz.Rect(line.get("bbox") or (0, 0, 0, 0))
                if prev_bottom is not None:
                    gap = rect.y0 - prev_bottom
                    if gap > max(6.0, (rect.y1 - rect.y0) * 0.9):
                        out.append(("", None))
                out.append((text, rect))
                prev_bottom = rect.y1
        if out:
            return _merge_same_row(out, fitz.Rect)
    except Exception as e:
        logger.warning(f"第 {page_no} 页按行提取失败，回退纯文本: {e}")
    return [
        (line.strip(), None)
        for line in (page.get_text("text") or "").splitlines()
        if line.strip()
    ]


def _pdf_page_blocks(fitz, page, page_no: int) -> tuple[list[tuple[str, object]], list[dict]]:
    """提取一页的正文行与表格块；表格识别失败时不丢弃内容。"""
    lines = _pdf_page_lines(fitz, page, page_no)
    tables: list[dict] = []
    try:
        finder = page.find_tables()
        found = list(getattr(finder, "tables", []) or [])
    except Exception as e:
        logger.warning(f"第 {page_no} 页表格识别不可用，正文按普通文本处理: {e}")
        found = []

    for tab in found:
        entry = {
            "page": page_no,
            "rows": [],
            "status": "fallback",
            "note": "",
            "header_rows": 0,
            "fallback_text": "",
        }
        try:
            entry["_rect"] = fitz.Rect(tab.bbox)
        except Exception:
            entry["_rect"] = None
        try:
            raw_rows = tab.extract() or []
            rows = [
                [" ".join(str(c).split()) if c is not None else "" for c in row]
                for row in raw_rows
            ]
            rows = _trim_table_rows(rows)
            if not rows or not any(any(c for c in row) for row in rows):
                raise ValueError("未提取到单元格文本")
            entry["rows"] = rows
            entry["status"] = "ok"
            entry["header_rows"] = 1
        except Exception as e:
            entry["note"] = f"表格结构提取失败，已保留该区域文本：{e}"
        tables.append(entry)

    kept: list[tuple[str, object]] = []
    for text, rect in lines:
        owner = None
        if text and rect is not None:
            point = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
            for entry in tables:
                box = entry.get("_rect")
                if box is not None and box.contains(point):
                    owner = entry
                    break
        if owner is not None:
            owner["fallback_text"] = (owner["fallback_text"] + " " + text).strip()
            continue
        kept.append((text, rect))

    result = []
    for entry in tables:
        box = entry.pop("_rect", None)
        if box is not None:
            entry["y"] = box.y0
        else:
            entry.pop("y", None)
        if not entry["rows"] and not entry["fallback_text"]:
            continue  # 检测到表格框但完全无可用文本，不生成空依据
        result.append(entry)
    return kept, result


def _pdf_body_geometry(pages: list[dict]) -> tuple[float, bool]:
    """返回正文左边界，以及是否存在可用的首行缩进。"""
    xs: list[float] = []
    for pg in pages:
        for text, rect in pg["lines"]:
            if not text or rect is None or _NOISE_RE.match(text.strip()):
                continue
            height = rect.y1 - rect.y0
            if 8.0 <= height <= 17.5:
                xs.append(rect.x0)
    if not xs:
        return 0.0, False
    left = min(xs)
    return left, any(x >= left + _PDF_INDENT_MIN for x in xs)


def _ends_sentence(text: str) -> bool:
    return (text or "").rstrip().endswith(("。", "！", "？", "；", ".", "!", "?", ";"))


def _detach_front_matter(paragraphs: list[dict], title: str) -> None:
    """首个章节前的论文标题（含英文标题）不参与正文改写。"""
    for p in paragraphs:
        if p.get("type") == "heading":
            break
        if p.get("type") != "body" or p.get("section"):
            continue
        text = (p.get("text") or "").strip()
        if not text:
            continue
        if text == title:
            p["type"] = "cover"
            continue
        if (len(text) <= 160 and not _ends_sentence(text)
                and not _ABSTRACT_LABEL_RE.match(text)
                and not _KEYWORDS_RE.match(text)
                and not _LIST_ITEM_RE.match(text)
                and not _REF_ITEM_RE.match(text)):
            p["type"] = "cover"


def parse_pdf(path: str, filename: str = "", progress=None,
              should_cancel=None) -> dict:
    """解析 PDF 为结构化 dict。

    Raises:
        ParseCancelled: 用户取消
        ValueError: 加密 / 页数超限 / 疑似扫描件 / 解析失败
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ValueError("PyMuPDF 未安装，无法解析 PDF") from e

    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ValueError(f"PDF 打开失败：{e}") from e

    with doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，请先解除密码保护后再上传")
        total_pages = len(doc)
        if total_pages > MAX_PAGES:
            raise ValueError(f"PDF 共 {total_pages} 页，超过 {MAX_PAGES} 页上限")
        _report(progress, 5, f"共 {total_pages} 页，正在提取文本…")
        pages: list[dict] = []
        for i in range(total_pages):
            _check_cancel(should_cancel)
            try:
                lines, page_tables = _pdf_page_blocks(fitz, doc[i], i + 1)
            except Exception as e:
                logger.warning(f"第 {i + 1} 页结构提取失败，按纯文本处理: {e}")
                lines = _pdf_page_lines(fitz, doc[i], i + 1)
                page_tables = []
            pages.append({"lines": lines, "tables": page_tables})
            _report(progress, 5 + int(55 * (i + 1) / max(1, total_pages)),
                    f"正在提取第 {i + 1}/{total_pages} 页…")
        metadata_title = (doc.metadata or {}).get("title") or ""

    full_text = "\n".join("\n".join(t for t, _ in pg["lines"]) for pg in pages)
    if len(re.sub(r"\s", "", full_text)) < MIN_TEXT_CHARS:
        raise ValueError("未提取到有效文本，疑似扫描件，需先 OCR 后再上传")

    paragraphs: list[dict] = []
    sections: list[dict] = []
    tables: list[dict] = []
    current_section: Optional[dict] = None
    buf_lines: list[str] = []
    buf_page = 1
    buf_is_ref = False
    counter = 0

    def add_paragraph(text: str, page: int, ptype: str, level: Optional[int] = None,
                      section: Optional[dict] = None, detached: bool = False) -> dict:
        nonlocal counter
        counter += 1
        pid = f"p_{counter:04d}"
        sec = None if detached else (section if section is not None else current_section)
        sec_title = sec["title"] if sec else ""
        sec_level = sec["level"] if sec else 0
        para = {
            "id": pid,
            "page": page,
            "section": sec_title,
            "level": level if level is not None else sec_level,
            "type": ptype,
            "text": text,
        }
        paragraphs.append(para)
        if sec is not None:
            sec["paragraph_ids"].append(pid)
        return para

    def flush() -> None:
        nonlocal buf_lines, buf_is_ref
        if not buf_lines:
            return
        text = _join_lines(buf_lines)
        buf_lines = []
        if not text:
            buf_is_ref = False
            return
        ptype = "reference" if (buf_is_ref or (
            current_section and "参考文献" in (current_section.get("title") or "")
        )) else "body"
        add_paragraph(text, buf_page, ptype)
        buf_is_ref = False

    page_base_section: Optional[dict] = None
    page_headings: list[tuple[Optional[float], dict]] = []

    def add_table(entry: dict, page_no: int) -> None:
        y = entry.get("y")
        sec = page_base_section
        for heading_y, heading_sec in page_headings:
            if y is None or heading_y is None or heading_y <= y:
                sec = heading_sec
            else:
                break
        rows = entry.get("rows") or []
        text_rows = [" | ".join(r) for r in rows]
        if not text_rows and entry.get("fallback_text"):
            text_rows = [entry["fallback_text"]]
        anchor = add_paragraph("\n".join(text_rows), page_no, "table",
                               section=sec) if text_rows else None
        tables.append({
            "id": f"t_{len(tables) + 1:04d}",
            "caption": "",
            "section_id": sec["id"] if sec else "",
            "section": sec["title"] if sec else "",
            "page": page_no,
            "rows": rows,
            "header_rows": int(entry.get("header_rows") or 0),
            "header_source": "",
            "units": "",
            "footnotes": [],
            "source": "pdf",
            "status": entry.get("status") or "ok",
            "note": entry.get("note") or "",
            "fallback_text": entry.get("fallback_text") or "",
            "paragraph_id": anchor["id"] if anchor else "",
        })

    in_toc = False
    cover_open = True
    cover_count = 0
    body_left, has_indent = _pdf_body_geometry(pages)
    last_caption_page: Optional[int] = None
    last_caption_bottom: Optional[float] = None

    for page_no, page in enumerate(pages, start=1):
        _check_cancel(should_cancel)
        _report(progress, 60 + int(35 * page_no / max(1, len(pages))),
                f"正在识别第 {page_no}/{len(pages)} 页的标题与段落…")
        page_base_section = current_section
        page_headings = []
        toc_from = _toc_start_index(page["lines"])
        pending_tables = sorted(
            page["tables"],
            key=lambda e: e.get("y") if e.get("y") is not None else float("inf"),
        )
        for line_idx, (raw, rect) in enumerate(page["lines"]):
            if rect is not None:
                while (pending_tables and pending_tables[0].get("y") is not None
                       and pending_tables[0]["y"] <= rect.y0):
                    flush()
                    add_table(pending_tables.pop(0), page_no)

            line = raw.strip()

            if toc_from is not None and line_idx >= toc_from:
                if line and not _NOISE_RE.match(line):
                    flush()
                    cover_open = False
                    add_paragraph(line, page_no, "toc", detached=True)
                continue

            if not line:
                if not has_indent:
                    flush()
                continue
            if _NOISE_RE.match(line):
                continue

            if _TOC_TITLE_RE.match(line):
                flush()
                in_toc = True
                cover_open = False
                add_paragraph(line, page_no, "toc", detached=True)
                continue

            if in_toc:
                if _is_toc_entry(line) or (
                        len(line) <= 100 and line[-1] not in "。！？；"
                        and not _detect_heading(line)):
                    flush()
                    add_paragraph(line, page_no, "toc", detached=True)
                    continue
                in_toc = False

            heading = _detect_heading(line)
            if heading:
                flush()
                cover_open = False
                level, title = heading
                current_section = {
                    "id": f"s{len(sections) + 1}",
                    "level": level,
                    "title": title,
                    "page": page_no,
                    "paragraph_ids": [],
                }
                sections.append(current_section)
                page_headings.append(
                    (rect.y0 if rect is not None else None, current_section)
                )
                add_paragraph(title, page_no, "heading", level=level)
                continue

            if _is_caption_line(line):
                flush()
                cover_open = False
                add_paragraph(line, page_no, "caption")
                if rect is not None:
                    last_caption_page = page_no
                    last_caption_bottom = rect.y1
                continue

            # 图题/表题的中文说明常被 PDF 拆成同一行的多个文本块
            if (last_caption_page == page_no and rect is not None
                    and last_caption_bottom is not None
                    and -18 <= rect.y0 - last_caption_bottom <= 14
                    and rect.x0 >= body_left + 40
                    and len(line) <= 80
                    and line[-1] not in "。！？；"
                    and not _detect_heading(line)):
                flush()
                cover_open = False
                add_paragraph(line, page_no, "caption")
                last_caption_bottom = rect.y1
                continue

            if _REF_ITEM_RE.match(line):
                flush()
                cover_open = False
                buf_page = page_no
                buf_is_ref = True
                buf_lines = [line]
                continue

            if cover_open and page_no == 1 and _is_cover_line(line, cover_count):
                flush()
                add_paragraph(line, page_no, "cover", detached=True)
                cover_count += 1
                continue
            cover_open = False

            if line in ("摘", "要", "关", "键", "词") and buf_lines:
                flush()

            if _ABSTRACT_LABEL_RE.match(line):
                current = _join_lines(buf_lines)
                if not (current in ("摘", "关", "键", "词") and line[0] in "要键"):
                    if current:
                        flush()
                elif current:
                    line = current + line
                    buf_lines = []
                    buf_page = page_no
                buf_is_ref = False

            if not buf_lines:
                buf_page = page_no
                buf_is_ref = False
            elif (_LIST_ITEM_RE.match(line) and not buf_is_ref
                  and not (current_section
                           and "参考文献" in (current_section.get("title") or ""))):
                flush()
                buf_page = page_no
            elif has_indent and rect is not None and rect.x0 >= body_left + _PDF_INDENT_MIN:
                current = _join_lines(buf_lines)
                if _ends_sentence(current) or _KEYWORDS_RE.match(current):
                    flush()
                    buf_page = page_no
                    buf_is_ref = False

            buf_lines.append(line)
            joined = _join_lines(buf_lines)
            if len(joined) >= _PDF_PARA_MAX_CHARS and _ends_sentence(joined):
                flush()

        while pending_tables:
            flush()
            add_table(pending_tables.pop(0), page_no)
        if page_no == 1:
            cover_open = False

    flush()
    _attach_table_meta(paragraphs, tables)
    paragraphs = _group_list_paragraphs(paragraphs, sections)
    _report(progress, 98, "正在整理章节与摘要…")

    title = _guess_title(metadata_title, paragraphs, filename or path)
    _detach_front_matter(paragraphs, title)
    abstract = _extract_abstract(sections, paragraphs, full_text)
    keywords = _extract_keywords(paragraphs)
    text_chars = sum(len(p["text"]) for p in paragraphs)
    _report(progress, 100, "解析完成")

    return {
        "title": title,
        "pages": total_pages,
        "text_chars": text_chars,
        "abstract": abstract,
        "keywords": keywords,
        "sections": sections,
        "paragraphs": paragraphs,
        "tables": tables,
    }


_DOCX_HEADING_STYLE_RE = re.compile(r"(?:heading|标题)\s*(\d)", re.IGNORECASE)


def _docx_heading_level(style_name: str) -> Optional[int]:
    m = _DOCX_HEADING_STYLE_RE.search(style_name or "")
    if not m:
        return None
    return max(1, min(int(m.group(1)), 4))


def _split_body_text(text: str) -> list[str]:
    """按句末标点把超长段落切开，避免单段过长影响诊断。"""
    if len(text) <= 700:
        return [text]
    pieces = [s for s in re.split(r"(?<=[。！？；])", text) if s.strip()]
    out: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) > 700:
            out.append(buf.strip())
            buf = piece
        else:
            buf += piece
    if buf.strip():
        out.append(buf.strip())
    return out or [text.strip()]


def _docx_header_rows(table) -> int:
    """读取 w:tblHeader 标记的表头行数；没有标记时返回 0。"""
    try:
        from docx.oxml.ns import qn
    except Exception:
        return 0
    count = 0
    for row in table.rows:
        tr_pr = row._tr.trPr
        if tr_pr is not None and tr_pr.find(qn("w:tblHeader")) is not None:
            count += 1
        else:
            break
    return count


def _docx_is_list(paragraph) -> bool:
    """Word 自动编号条目的文字里没有编号，靠 numPr 识别。"""
    try:
        p_pr = paragraph._p.pPr
        return bool(p_pr is not None and p_pr.numPr is not None)
    except Exception:
        return False


def _iter_docx_blocks(doc):
    """按文档顺序遍历段落与表格。"""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def parse_docx(path: str, filename: str = "", progress=None,
               should_cancel=None) -> dict:
    """解析 .docx 为结构化 dict，输出与 parse_pdf 相同。

    Word 没有可靠的页码信息，pages 按字符数估算。
    """
    try:
        import docx
    except ImportError as e:
        raise ValueError("python-docx 未安装，无法解析 Word") from e

    try:
        doc = docx.Document(path)
    except Exception as e:
        raise ValueError(f"Word 打开失败：{e}") from e

    try:
        metadata_title = (doc.core_properties.title or "").strip()
    except Exception:
        metadata_title = ""

    paragraphs: list[dict] = []
    sections: list[dict] = []
    tables: list[dict] = []
    current_section: Optional[dict] = None
    buf_lines: list[str] = []
    buf_is_ref = False
    counter = 0
    chars_so_far = 0

    def add_paragraph(raw: str, ptype: str, level: Optional[int] = None,
                      detached: bool = False) -> Optional[dict]:
        nonlocal counter, chars_so_far
        text = raw.strip()
        if not text:
            return None
        counter += 1
        pid = f"p_{counter:04d}"
        page = chars_so_far // 1000 + 1
        sec = None if detached else current_section
        para = {
            "id": pid,
            "page": page,
            "section": sec["title"] if sec else "",
            "level": level if level is not None else (sec["level"] if sec else 0),
            "type": ptype,
            "text": text,
        }
        paragraphs.append(para)
        chars_so_far += len(re.sub(r"\s", "", text))
        if sec is not None:
            sec["paragraph_ids"].append(pid)
        return para

    def flush() -> None:
        nonlocal buf_lines, buf_is_ref
        if not buf_lines:
            return
        text = _join_lines([x for line in buf_lines for x in line.split("\n")])
        buf_lines = []
        is_ref = buf_is_ref or bool(
            current_section and "参考文献" in (current_section.get("title") or "")
        )
        for chunk in _split_body_text(text):
            add_paragraph(chunk, "reference" if is_ref else "body")
        buf_is_ref = False

    def add_table(table, header_rows: int) -> None:
        flush()
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([(cell.text or "").strip() for cell in row.cells])
        rows = _trim_table_rows(rows)
        if not rows:
            return
        page = chars_so_far // 1000 + 1
        anchor = add_paragraph("\n".join(" | ".join(r) for r in rows), "table")
        tables.append({
            "id": f"t_{len(tables) + 1:04d}",
            "caption": "",
            "section_id": current_section["id"] if current_section else "",
            "section": current_section["title"] if current_section else "",
            "page": page,
            "rows": rows,
            "header_rows": header_rows or 1,
            "header_source": "tblHeader" if header_rows else "first-row",
            "units": "",
            "footnotes": [],
            "source": "docx",
            "status": "ok",
            "note": "",
            "fallback_text": "",
            "paragraph_id": anchor["id"] if anchor else "",
        })

    def start_section(title: str, level: int) -> None:
        nonlocal current_section
        flush()
        current_section = {
            "id": f"s{len(sections) + 1}",
            "level": level,
            "title": title,
            "page": chars_so_far // 1000 + 1,
            "paragraph_ids": [],
        }
        sections.append(current_section)
        add_paragraph(title, "heading", level=level)

    total_blocks = max(1, len(doc.element.body))
    _report(progress, 5, "正在读取 Word 段落与表格…")
    in_toc = False
    cover_open = True
    cover_count = 0
    for idx, block in enumerate(_iter_docx_blocks(doc), start=1):
        _check_cancel(should_cancel)
        if idx % 5 == 0 or idx == total_blocks:
            _report(progress, 5 + int(90 * idx / total_blocks),
                    f"正在解析第 {idx}/{total_blocks} 个内容块…")
        if hasattr(block, "rows"):  # Table
            cover_open = False
            add_table(block, _docx_header_rows(block))
            continue

        text = (block.text or "").strip()
        if not text:
            flush()
            continue

        style_name = getattr(getattr(block, "style", None), "name", "") or ""
        if _TOC_TITLE_RE.match(text):
            flush()
            in_toc = True
            cover_open = False
            add_paragraph(text, "toc", detached=True)
            continue

        if _TOC_STYLE_RE.match(style_name):
            flush()
            in_toc = True
            add_paragraph(text, "toc", detached=True)
            continue

        if in_toc:
            if (_is_toc_entry(text)
                    or (len(text) <= 100 and text[-1] not in "。！？；"
                        and not _detect_heading(text))):
                flush()
                add_paragraph(text, "toc", detached=True)
                continue
            in_toc = False

        style_level = _docx_heading_level(style_name)
        if style_level is not None:
            if cover_open and _is_cover_heading(text, metadata_title, idx):
                flush()
                add_paragraph(text, "cover", detached=True)
                cover_count += 1
                continue
            cover_open = False
            start_section(text.rstrip(" :："), style_level)
            continue

        first_line = text.split("\n", 1)[0].strip()
        if _NOISE_RE.match(first_line) and len(text.split("\n")) == 1:
            flush()
            continue

        heading = _detect_heading(first_line) if "\n" not in text else None
        if heading:
            cover_open = False
            start_section(heading[1], heading[0])
            continue

        if _is_caption_line(first_line):
            flush()
            cover_open = False
            add_paragraph(text, "caption")
            continue

        if _REF_ITEM_RE.match(first_line):
            flush()
            cover_open = False
            buf_is_ref = True
            buf_lines = [text]
            continue

        if cover_open and _is_cover_line(text, cover_count):
            flush()
            add_paragraph(text, "cover", detached=True)
            cover_count += 1
            continue
        cover_open = False

        num_list = _docx_is_list(block)
        offset = len(paragraphs)
        buf_lines.append(text)
        flush()
        if num_list:
            for para in paragraphs[offset:]:
                if para.get("type") == "body":
                    para["list"] = True

    flush()
    _attach_table_meta(paragraphs, tables)
    paragraphs = _group_list_paragraphs(paragraphs, sections)

    title = _guess_title(metadata_title, paragraphs, filename or path)
    _detach_front_matter(paragraphs, title)
    full_text = "\n".join(p["text"] for p in paragraphs)
    abstract = _extract_abstract(sections, paragraphs, full_text)
    keywords = _extract_keywords(paragraphs)
    text_chars = sum(len(re.sub(r"\s", "", p["text"])) for p in paragraphs)
    if text_chars < MIN_TEXT_CHARS:
        raise ValueError("未提取到有效文本，请确认 Word 文档包含正文内容")
    _report(progress, 100, "解析完成")

    return {
        "title": title,
        "pages": max(1, math.ceil(text_chars / 1000)),
        "text_chars": text_chars,
        "abstract": abstract,
        "keywords": keywords,
        "sections": sections,
        "paragraphs": paragraphs,
        "tables": tables,
    }
