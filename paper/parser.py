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
_CN_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+章[\s、.．:：]*(.*)$")

_UNNUMBERED_HEADINGS = {
    "摘要", "abstract", "引言", "绪论", "前言", "结论", "总结与展望",
    "结论与展望", "研究背景", "参考文献", "references", "致谢",
    "acknowledgements", "acknowledgments", "附录", "appendix",
}

_CAPTION_RE = re.compile(r"^(图|表)\s*\d+([\-–—.]\d+)?|^(fig(?:ure)?\.?|table)\s*\d+", re.IGNORECASE)
_REF_ITEM_RE = re.compile(r"^\[\s*\d+\s*\]")
_KEYWORDS_RE = re.compile(r"(?:关\s*键\s*词|关键字|key\s*words?|keywords)\s*[:：]\s*(.+)", re.IGNORECASE)


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
    if low in _UNNUMBERED_HEADINGS:
        return 1, text.rstrip(" :：")

    m = _CN_CHAPTER_RE.match(text)
    if m:
        return 1, text.rstrip(" :：")

    m = _NUM_HEADING_RE.match(text)
    if m:
        num, rest = m.group(1), m.group(2).strip()
        if not rest or len(rest) > 48:
            return None
        if "，" in rest or "," in rest:
            return None
        if rest[0] in "年月日%％":
            return None
        level = min(num.count(".") + 1, 4)
        return level, f"{num} {rest}"
    return None


def _guess_title(metadata_title: str, paragraphs: list[dict], filename: str) -> str:
    mt = (metadata_title or "").strip()
    if len(mt) >= 4 and mt.lower() not in ("untitled", "无标题", "title"):
        return mt[:200]
    generic = {"摘要", "abstract", "引言", "绪论", "目录", "参考文献", "致谢"}
    for p in paragraphs[:6]:
        t = (p.get("text") or "").strip()
        if 4 <= len(t) <= 120 and p.get("type") != "reference" and t.rstrip(" :：").lower() not in generic:
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
        pages = []
        for i in range(total_pages):
            _check_cancel(should_cancel)
            pages.append(doc[i].get_text("text") or "")
            _report(progress, 5 + int(55 * (i + 1) / max(1, total_pages)),
                    f"正在提取第 {i + 1}/{total_pages} 页…")
        metadata_title = (doc.metadata or {}).get("title") or ""

    full_text = "\n".join(pages)
    if len(re.sub(r"\s", "", full_text)) < MIN_TEXT_CHARS:
        raise ValueError("未提取到有效文本，疑似扫描件，需先 OCR 后再上传")

    paragraphs: list[dict] = []
    sections: list[dict] = []
    current_section: Optional[dict] = None
    buf_lines: list[str] = []
    buf_page = 1
    buf_is_ref = False
    counter = 0

    def add_paragraph(text: str, page: int, ptype: str, level: Optional[int] = None) -> dict:
        nonlocal counter
        counter += 1
        pid = f"p_{counter:04d}"
        sec_title = current_section["title"] if current_section else ""
        sec_level = current_section["level"] if current_section else 0
        para = {
            "id": pid,
            "page": page,
            "section": sec_title,
            "level": level if level is not None else sec_level,
            "type": ptype,
            "text": text,
        }
        paragraphs.append(para)
        if current_section is not None:
            current_section["paragraph_ids"].append(pid)
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

    for page_no, page_text in enumerate(pages, start=1):
        _check_cancel(should_cancel)
        _report(progress, 60 + int(35 * page_no / max(1, len(pages))),
                f"正在识别第 {page_no}/{len(pages)} 页的标题与段落…")
        for raw in page_text.splitlines():
            line = raw.strip()
            if not line or _NOISE_RE.match(line):
                flush()
                continue

            heading = _detect_heading(line)
            if heading:
                flush()
                level, title = heading
                current_section = {
                    "id": f"s{len(sections) + 1}",
                    "level": level,
                    "title": title,
                    "page": page_no,
                    "paragraph_ids": [],
                }
                sections.append(current_section)
                add_paragraph(title, page_no, "heading", level=level)
                continue

            if _CAPTION_RE.match(line):
                flush()
                add_paragraph(line, page_no, "caption")
                continue

            if _REF_ITEM_RE.match(line):
                flush()
                buf_page = page_no
                buf_is_ref = True
                buf_lines = [line]
                continue

            if not buf_lines:
                buf_page = page_no
                buf_is_ref = False
            buf_lines.append(line)
            joined = _join_lines(buf_lines)
            if len(joined) >= 700 or line[-1] in "。！？；":
                flush()

    flush()
    _report(progress, 98, "正在整理章节与摘要…")

    title = _guess_title(metadata_title, paragraphs, filename or path)
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
    current_section: Optional[dict] = None
    buf_lines: list[str] = []
    buf_is_ref = False
    counter = 0
    chars_so_far = 0

    def add_paragraph(raw: str, ptype: str, level: Optional[int] = None) -> None:
        nonlocal counter, chars_so_far
        text = raw.strip()
        if not text:
            return
        counter += 1
        pid = f"p_{counter:04d}"
        page = chars_so_far // 1000 + 1
        paragraphs.append({
            "id": pid,
            "page": page,
            "section": current_section["title"] if current_section else "",
            "level": level if level is not None else (current_section["level"] if current_section else 0),
            "type": ptype,
            "text": text,
        })
        chars_so_far += len(re.sub(r"\s", "", text))
        if current_section is not None:
            current_section["paragraph_ids"].append(pid)

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
    for idx, block in enumerate(_iter_docx_blocks(doc), start=1):
        _check_cancel(should_cancel)
        if idx % 5 == 0 or idx == total_blocks:
            _report(progress, 5 + int(90 * idx / total_blocks),
                    f"正在解析第 {idx}/{total_blocks} 个内容块…")
        if hasattr(block, "rows"):  # Table
            for row in block.rows:
                cells: list[str] = []
                for cell in row.cells:
                    val = cell.text.strip()
                    if val and (not cells or cells[-1] != val):
                        cells.append(val)
                line = " | ".join(cells)
                if line:
                    buf_lines.append(line)
                    flush()
            continue

        text = (block.text or "").strip()
        if not text:
            flush()
            continue

        style_level = _docx_heading_level(getattr(getattr(block, "style", None), "name", "") or "")
        if style_level is not None:
            start_section(text.rstrip(" :："), style_level)
            continue

        first_line = text.split("\n", 1)[0].strip()
        if _NOISE_RE.match(first_line) and len(text.split("\n")) == 1:
            flush()
            continue

        heading = _detect_heading(first_line) if "\n" not in text else None
        if heading:
            start_section(heading[1], heading[0])
            continue

        if _CAPTION_RE.match(first_line):
            flush()
            add_paragraph(text, "caption")
            continue

        if _REF_ITEM_RE.match(first_line):
            flush()
            buf_is_ref = True
            buf_lines = [text]
            continue

        buf_lines.append(text)
        flush()

    flush()

    title = _guess_title(metadata_title, paragraphs, filename or path)
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
    }
