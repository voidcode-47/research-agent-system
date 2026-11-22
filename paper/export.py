"""按需求格式导出 Word（python-docx）/ PDF（reportlab）。

中文字体必须通过 qn('w:eastAsia') 设置，否则 Word 中不生效。
"""
import datetime
import difflib
import os
import re
from io import BytesIO
from xml.sax.saxutils import escape

from config.paper import (
    DEFAULT_FORMAT,
    DIAGNOSIS_DIMENSIONS,
    TEMPLATE_FORMAT_DEFAULTS,
)
from paper import store
from utils.atomic_file import atomic_write
from utils.logger import get_logger

logger = get_logger(__name__)


def _effective_format(requirement: dict | None) -> dict:
    """合并需求格式与模板默认值；仍是通用默认值的字段按所选模板排版。"""
    user = dict(((requirement or {}).get("format")) or {})
    template = user.get("template") or DEFAULT_FORMAT["template"]
    overrides = TEMPLATE_FORMAT_DEFAULTS.get(template) or {}
    fmt = dict(DEFAULT_FORMAT)
    fmt.update(overrides)
    # 需求已在表单确认并带上 explicit：用户看到的每个值都来自表单，直接按值生效，
    # 否则手动改回默认值的字段会被模板默认值再次覆盖；表单没有的字段仍用模板值。
    if user.get("explicit"):
        fmt = dict(DEFAULT_FORMAT)
        fmt.update(overrides)
        fmt.update({k: v for k, v in user.items() if k in DEFAULT_FORMAT})
        fmt["template"] = template
        return fmt
    for key, value in user.items():
        if key in overrides and value == DEFAULT_FORMAT.get(key):
            continue
        fmt[key] = value
    fmt["template"] = template
    return fmt


def _save_export(paper_id: str, filename: str, buf: BytesIO) -> None:
    """尽力保存导出副本；磁盘不可写时仍可下载内存中的结果。"""
    try:
        with store.locked():
            store.load_meta(paper_id)
            out_dir = os.path.join(store.paper_dir(paper_id), "exports")
            atomic_write(os.path.join(out_dir, filename), buf.getvalue())
    except (OSError, ValueError):
        pass


def _set_run_font(run, font_name: str, size_pt: float, bold: bool = False,
                  east_asia: str | None = None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), east_asia or font_name)
    rfonts.set(qn("w:ascii"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)


def _set_style_font(style, font_name: str, size_pt: float,
                    east_asia: str | None = None) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    style.font.name = font_name
    style.font.size = Pt(size_pt)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), east_asia or font_name)
    rfonts.set(qn("w:ascii"), font_name)
    rfonts.set(qn("w:hAnsi"), font_name)


def _add_docx_table(doc, table: dict, font_name: str, size: float,
                    east_asia: str | None = None) -> None:
    rows = table.get("rows") or []
    if not rows:
        fallback = str(table.get("fallback_text") or "").strip()
        if fallback:
            para = doc.add_paragraph()
            run = para.add_run(fallback)
            _set_run_font(run, font_name, size, east_asia=east_asia)
        return
    cols = max(len(r) for r in rows)
    if cols <= 0:
        return
    header_rows = max(0, int(table.get("header_rows") or 0))
    out = doc.add_table(rows=len(rows), cols=cols)
    out.style = "Table Grid"
    for i, row in enumerate(rows):
        for j in range(cols):
            cell = out.rows[i].cells[j]
            cell.text = ""
            value = str(row[j]) if j < len(row) else ""
            run = cell.paragraphs[0].add_run(value)
            _set_run_font(run, font_name, max(8, size - 1), bold=i < header_rows,
                          east_asia=east_asia)


def _add_list_docx(doc, text: str, font_name: str, size: float, spacing: float,
                   east_asia: str | None = None) -> None:
    """列表块在平台内是一段，导出时按条目还原为独立段落。"""
    from docx.shared import Pt

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        para = doc.add_paragraph()
        para.paragraph_format.line_spacing = spacing
        para.paragraph_format.first_line_indent = Pt(size * 2)
        run = para.add_run(line)
        _set_run_font(run, font_name, size, east_asia=east_asia)


def _add_revision_run(para, text: str, kind: str, font_name: str, size: float,
                      bold: bool, rev_id: int, date: str) -> None:
    """把一段文字写成 Word 修订（w:ins / w:del），便于在 Word 中接受或拒绝。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = para.add_run()
    _set_run_font(run, font_name, size, bold)
    element = run._element
    text_el = OxmlElement("w:delText" if kind == "del" else "w:t")
    text_el.set(qn("xml:space"), "preserve")
    text_el.text = text
    rpr = element.find(qn("w:rPr"))
    if rpr is not None:
        rpr.addnext(text_el)
    else:
        element.insert(0, text_el)
    wrapper = OxmlElement("w:ins" if kind == "ins" else "w:del")
    wrapper.set(qn("w:id"), str(rev_id))
    wrapper.set(qn("w:author"), "论文助手")
    wrapper.set(qn("w:date"), date)
    element.addprevious(wrapper)
    wrapper.append(element)


def _add_tracked_paragraph(doc, old_text: str, new_text: str, font_name: str,
                           size: float, spacing: float, rev_id: int, date: str,
                           bold: bool = False, first_indent: bool = False,
                           center: bool = False, heading_level: int = 0) -> None:
    """一段同时包含原文删除与改写后插入的修订段落。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    if heading_level:
        para = doc.add_paragraph(style=f"Heading {heading_level}")
    else:
        para = doc.add_paragraph()
    para.paragraph_format.line_spacing = spacing
    if center:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if first_indent and not heading_level:
        para.paragraph_format.first_line_indent = Pt(size * 2)
    _add_revision_run(para, old_text, "del", font_name, size, bold, rev_id, date)
    _add_revision_run(para, new_text, "ins", font_name, size, bold, rev_id + 1, date)


# ---------------------------------------------------------------------------
# 原文保留：在原 docx 上写修订，保留封面、目录、图片、表格与原有字体
# ---------------------------------------------------------------------------
_FONT_ALIASES = {
    "simsun": "宋体",
    "songti sc": "宋体",
    "stsong": "宋体",
    "nsimsun": "新宋体",
    "fangsong": "仿宋",
    "fangsong_gb2312": "仿宋",
    "仿宋_gb2312": "仿宋",
    "kaiti": "楷体",
    "kaiti_gb2312": "楷体",
    "楷体_gb2312": "楷体",
    "simhei": "黑体",
    "microsoft yahei": "微软雅黑",
}
_REV_AUTHOR = "论文助手"


def _normalize_font_name(name: str) -> str:
    raw = (name or "").strip()
    return _FONT_ALIASES.get(raw.lower(), raw)


def _original_docx_path(paper_id: str) -> str | None:
    """返回上传时保存的原始 docx；PDF 论文或文件缺失时返回 None。"""
    try:
        path = os.path.join(store.paper_dir(paper_id), "original.docx")
    except (ValueError, OSError):
        return None
    return path if os.path.isfile(path) else None


def _rpr_font(rpr, theme: dict, script: str = "eastAsia") -> tuple[str, float | None]:
    """从 rPr 读取字体与字号（half-point 转磅）。

    script="eastAsia" 读取中文字体；script="latin" 读取英文/数字字体。
    """
    from docx.oxml.ns import qn

    if rpr is None:
        return "", None
    fonts = rpr.find(qn("w:rFonts"))
    name = ""
    if fonts is not None:
        if script == "latin":
            name = (fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi")) or "").strip()
            key = fonts.get(qn("w:asciiTheme")) or fonts.get(qn("w:hAnsiTheme"))
        else:
            name = (fonts.get(qn("w:eastAsia")) or "").strip()
            key = fonts.get(qn("w:eastAsiaTheme"))
        if not name and key:
            name = theme.get(key, "")
    size = None
    sz = rpr.find(qn("w:sz"))
    if sz is not None:
        try:
            size = float(sz.get(qn("w:val"))) / 2.0
        except (TypeError, ValueError):
            size = None
    return name, size


def _element_rpr(element):
    from docx.oxml.ns import qn

    return element.find(qn("w:rPr")) if element is not None else None


def _doc_defaults_rpr(doc):
    from docx.oxml.ns import qn

    try:
        styles = doc.styles.element
    except Exception:
        return None
    node = styles.find(qn("w:docDefaults"))
    node = node.find(qn("w:rPrDefault")) if node is not None else None
    return node.find(qn("w:rPr")) if node is not None else None


def _theme_font_map(doc) -> dict:
    """解析主题字体，用于 rFonts 只写 *Theme 的情况。"""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import parse_xml

    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    out: dict[str, str] = {}
    try:
        part = doc.part.part_related_by(RT.THEME)
        root = parse_xml(part.blob)
    except Exception:
        return out
    for key in ("major", "minor"):
        node = root.find(f".//{ns}{key}Font")
        if node is None:
            continue
        latin = node.find(f"{ns}latin")
        east = node.find(f"{ns}ea")
        if latin is not None and latin.get("typeface"):
            out[f"{key}Ascii"] = latin.get("typeface")
            out[f"{key}HAnsi"] = latin.get("typeface")
        if east is not None and east.get("typeface"):
            out[f"{key}EastAsia"] = east.get("typeface")
    return out


def _effective_run_font(run, paragraph, theme: dict, defaults,
                        script: str = "eastAsia") -> tuple[str, float | None]:
    """run → 段落样式链 → docDefaults，逐层补齐字体与字号。"""
    name, size = _rpr_font(run._element.rPr, theme, script)
    if not name or size is None:
        style = getattr(paragraph, "style", None)
        seen: set[int] = set()
        while style is not None and id(style) not in seen:
            seen.add(id(style))
            style_name, style_size = _rpr_font(_element_rpr(style.element), theme, script)
            if not name:
                name = style_name
            if size is None:
                size = style_size
            if name and size is not None:
                break
            style = getattr(style, "base_style", None)
    if not name or size is None:
        default_name, default_size = _rpr_font(defaults, theme, script)
        if not name:
            name = default_name
        if size is None:
            size = default_size
    return name, size


def _is_font_sample(paragraph) -> bool:
    text = (paragraph.text or "").strip()
    if len(text) < 20 or not re.search(r"[\u4e00-\u9fff]", text):
        return False
    style_name = (getattr(getattr(paragraph, "style", None), "name", "") or "")
    if re.match(r"^(?:toc|目录|heading|标题)", style_name, re.IGNORECASE):
        return False
    return not re.match(r"^(?:图|表|fig(?:ure)?|table)\s*\d", text, re.IGNORECASE)


def _collect_font_stats(doc, theme: dict, defaults, script: str) -> list[dict]:
    """按字符数统计正文主要字体；中英文分别统计。"""
    stats: dict[tuple, dict] = {}
    for paragraph in doc.paragraphs:
        if not _is_font_sample(paragraph):
            continue
        for run in paragraph.runs:
            text = run.text or ""
            if script == "latin":
                count = len(re.findall(r"[A-Za-z0-9]", text))
            else:
                count = len(re.findall(r"[\u4e00-\u9fff]", text))
            if not count:
                continue
            name, size = _effective_run_font(run, paragraph, theme, defaults, script)
            if not name and size is None:
                continue
            key = (name or "未指定", None if size is None else round(size, 1))
            item = stats.setdefault(
                key, {"font": key[0], "size": key[1], "chars": 0}
            )
            item["chars"] += count
    total = sum(item["chars"] for item in stats.values())
    ranked = sorted(stats.values(), key=lambda x: x["chars"], reverse=True)[:5]
    for item in ranked:
        item["ratio"] = round(item["chars"] / total, 3) if total else 0
    return ranked


def _paragraph_attr(paragraph, attr: str):
    """读取段落属性，未直接设置时沿样式链继承。"""
    value = getattr(paragraph.paragraph_format, attr, None)
    if value is not None:
        return value
    style = getattr(paragraph, "style", None)
    seen: set[int] = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        value = getattr(style.paragraph_format, attr, None)
        if value is not None:
            return value
        style = getattr(style, "base_style", None)
    return None


def _line_spacing_ratio(paragraph, size: float) -> float | None:
    value = _paragraph_attr(paragraph, "line_spacing")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    pt = getattr(value, "pt", None)
    if pt and size:
        return round(pt / size, 2)
    return None


def _spacing_pt(paragraph, attr: str) -> float | None:
    value = _paragraph_attr(paragraph, attr)
    pt = getattr(value, "pt", None)
    return round(pt, 1) if pt is not None else None


def _dominant(values: list) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    counts: dict = {}
    for value in clean:
        key = round(float(value), 1)
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=counts.get)


def _heading_level(paragraph) -> int:
    from docx.oxml.ns import qn

    try:
        style_id = paragraph._p.pPr.pStyle.val if paragraph._p.pPr is not None \
            and paragraph._p.pPr.find(qn("w:pStyle")) is not None else ""
    except Exception:
        style_id = ""
    name = (getattr(getattr(paragraph, "style", None), "name", "") or "")
    for candidate in (name, style_id):
        m = re.match(r"^(?:heading|标题)\s*(\d)", candidate.strip(), re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 0


def _has_page_break_before(paragraph) -> bool:
    """标题前是否有分页：pageBreakBefore 或上一段末尾的手动分页符。"""
    from docx.oxml.ns import qn

    if paragraph.paragraph_format.page_break_before:
        return True
    node = paragraph._p.getprevious()
    hops = 0
    while node is not None and hops < 3:
        hops += 1
        for br in node.iter(qn("w:br")):
            if br.get(qn("w:type")) == "page":
                return True
        text = "".join(t.text or "" for t in node.iter(qn("w:t"))).strip()
        if text:
            break
        node = node.getprevious()
    return False


def inspect_original_format(paper_id: str, requirement: dict | None = None) -> dict:
    """检查原文正文的中英文字体、字号、行距、段间距与章节分页是否符合论文规范。"""
    fmt = _effective_format(requirement)
    required_cn = str(fmt.get("font_body") or "宋体")
    required_latin = str(fmt.get("font_latin") or "Times New Roman")
    required_size = float(fmt.get("font_size_body_pt") or 12)
    required_line = float(fmt.get("line_spacing") or 1.5)
    required_before = float(fmt.get("space_before_pt") or 0)
    required_after = float(fmt.get("space_after_pt") or 0)
    required_indent = float(fmt.get("first_line_indent_chars") or 0)
    required_page_break = bool(fmt.get("page_break_before_heading"))
    result = {
        "has_original_docx": False,
        "required": {
            "template": fmt.get("template"),
            "font_body": required_cn,
            "font_latin": required_latin,
            "font_size_body_pt": required_size,
            "line_spacing": required_line,
            "space_before_pt": required_before,
            "space_after_pt": required_after,
            "first_line_indent_chars": required_indent,
            "page_break_before_heading": required_page_break,
        },
        "fonts": [],
        "latin_fonts": [],
        "compliant": True,
        "issues": [],
    }
    path = _original_docx_path(paper_id)
    if not path:
        return result
    try:
        from docx import Document

        doc = Document(path)
    except Exception as e:
        logger.warning("检查原文格式失败: %s", e)
        return result

    result["has_original_docx"] = True
    theme = _theme_font_map(doc)
    defaults = _doc_defaults_rpr(doc)
    cn_ranked = _collect_font_stats(doc, theme, defaults, "eastAsia")
    en_ranked = _collect_font_stats(doc, theme, defaults, "latin")
    result["fonts"] = cn_ranked
    result["latin_fonts"] = en_ranked

    if not cn_ranked and not en_ranked:
        return result

    def _check_font(ranked: list[dict], required: str, label: str) -> None:
        if not ranked:
            return
        dominant = ranked[0]
        if _normalize_font_name(dominant["font"]) != _normalize_font_name(required):
            result["issues"].append(
                f"正文主要{label}为 {dominant['font']}，论文规范要求 {required}"
            )
        families = []
        for item in ranked:
            family = _normalize_font_name(item["font"])
            if family and family not in families:
                families.append(family)
        if len(families) > 1 and ranked[1]["ratio"] >= 0.15:
            result["issues"].append(f"正文存在多种{label}：{'、'.join(families[:3])}")

    _check_font(cn_ranked, required_cn, "中文字体")
    _check_font(en_ranked, required_latin, "英文字体")

    body = [p for p in doc.paragraphs if _is_font_sample(p) and len(p.text.strip()) >= 20]
    if body:
        sizes = [float(_effective_run_font(p.runs[0], p, theme, defaults)[1]
                       or required_size) for p in body if p.runs]
        dominant_size = _dominant(sizes)
        if dominant_size is not None and abs(dominant_size - required_size) > 0.4:
            result["issues"].append(
                f"正文主要字号为 {dominant_size:g} 磅，论文规范要求 {required_size:g} 磅"
            )
        line = _dominant([_line_spacing_ratio(p, required_size) for p in body])
        if line is not None and abs(line - required_line) > 0.1:
            result["issues"].append(
                f"正文行距约为 {line:g} 倍，论文规范要求 {required_line:g} 倍"
            )
        before = _dominant([_spacing_pt(p, "space_before") for p in body])
        after = _dominant([_spacing_pt(p, "space_after") for p in body])
        if before is not None and abs(before - required_before) > 2:
            result["issues"].append(
                f"正文段前间距约为 {before:g} 磅，论文规范要求 {required_before:g} 磅"
            )
        if after is not None and abs(after - required_after) > 2:
            result["issues"].append(
                f"正文段后间距约为 {after:g} 磅，论文规范要求 {required_after:g} 磅"
            )
        indents = []
        for p in body:
            value = _paragraph_attr(p, "first_line_indent")
            pt = getattr(value, "pt", None)
            if pt is not None:
                indents.append(round(pt / required_size, 1))
        indent = _dominant(indents) if indents else None
        if required_indent > 0 and indent is not None and abs(indent - required_indent) > 0.4:
            result["issues"].append(
                f"正文首行缩进约为 {indent:g} 字符，论文规范要求 {required_indent:g} 字符"
            )
    if required_page_break:
        headings = [p for p in doc.paragraphs if _heading_level(p) == 1]
        if len(headings) >= 2 and not any(_has_page_break_before(p) for p in headings):
            result["issues"].append("一级标题未设置“另起一页”（每章应另起一页）")

    result["compliant"] = not result["issues"]
    return result


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _docx_text_paragraphs(doc) -> list[tuple]:
    """按文档顺序返回正文非空段落（不含表格内段落）。"""
    from docx.oxml.text.paragraph import CT_P
    from docx.text.paragraph import Paragraph

    out = []
    for child in doc.element.body.iterchildren():
        if not isinstance(child, CT_P):
            continue
        paragraph = Paragraph(child, doc)
        norm = _norm_text(paragraph.text)
        if norm:
            out.append((paragraph, norm))
    return out


def _align_base_paragraphs(base: list[dict], entries: list[tuple]):
    """把 v0 段落按顺序对齐到原 docx 段落。

    返回 (pid → docx 段落索引列表, docx 段落索引 → [(pid, 行号)])。
    行号为 None 表示整段对应；非 None 用于合并后的列表段落。
    """
    pid_entries: dict[str, list[int]] = {}
    entry_links: dict[int, list[tuple[str, int | None]]] = {}
    consumed = [0] * len(entries)
    cursor = 0
    total = len(entries)
    for paragraph in base or []:
        pid = paragraph.get("id")
        text = (paragraph.get("text") or "").strip()
        if not pid or not text or paragraph.get("type") == "table":
            continue
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            continue
        multi_line = len(lines) > 1
        indexes: list[int] = []
        matched = True
        for line in lines:
            needle = _norm_text(line)
            if not needle:
                continue
            found = None
            for k in range(cursor, min(total, cursor + 12)):
                if entries[k][1].startswith(needle, consumed[k]):
                    found = k
                    break
            if found is None:
                matched = False
                break
            consumed[found] += len(needle)
            indexes.append(found)
            cursor = found + 1 if consumed[found] >= len(entries[found][1]) else found
        if not matched or not indexes:
            continue
        pid_entries[pid] = indexes
        for line_index, index in enumerate(indexes):
            entry_links.setdefault(index, []).append(
                (pid, line_index if multi_line else None)
            )
    return pid_entries, entry_links


def _entry_new_text(links: list[tuple[str, int | None]],
                    current_map: dict[str, dict]) -> str:
    """按映射计算某个原文段落在导出稿中的文本；段落被删除时返回空串。"""
    chunks: list[str] = []
    for pid, line_index in links:
        current = current_map.get(pid)
        if current is None:
            continue
        text = (current.get("text") or "").strip()
        if line_index is None:
            chunks.append(text)
        else:
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            chunks.append(lines[line_index] if line_index < len(lines) else "")
    return "".join(chunks)


def _clean_rpr(rpr):
    from copy import deepcopy

    from docx.oxml.ns import qn

    if rpr is None:
        return None
    clone = deepcopy(rpr)
    for tag in ("w:rPrChange", "w:ins", "w:del", "w:moveFrom", "w:moveTo"):
        el = clone.find(qn(tag))
        if el is not None:
            clone.remove(el)
    return clone


def _set_revision_attrs(element, rev_id: int, date: str) -> None:
    from docx.oxml.ns import qn

    element.set(qn("w:id"), str(rev_id))
    element.set(qn("w:author"), _REV_AUTHOR)
    element.set(qn("w:date"), date)


def _diff_tokens(text: str) -> list[str]:
    """按英文单词/数字、空白和单个汉字切分，供细粒度修订使用。"""
    return re.findall(r"[A-Za-z0-9_./%+\-]+|\s+|.", text or "")


def _rpr_key(rpr) -> bytes:
    from lxml import etree

    return b"" if rpr is None else etree.tostring(rpr)


def _text_runs(p):
    """返回段落中带文字的 run，跳过图片/对象 run。"""
    from docx.oxml.ns import qn

    runs = []
    for run in p.iter(qn("w:r")):
        if (run.find(qn("w:drawing")) is not None
                or run.find(qn("w:pict")) is not None
                or run.find(qn("w:object")) is not None):
            continue
        if run.find(qn("w:t")) is not None:
            runs.append(run)
    return runs


def _run_char_rprs(text_runs) -> tuple[str, list]:
    """按字符记录原文每个字符所属 run 的字体格式，用于保留行内格式。"""
    from docx.oxml.ns import qn

    chars: list[str] = []
    rprs: list = []
    for run in text_runs:
        text = "".join(node.text or "" for node in run.findall(qn("w:t")))
        if not text:
            continue
        chars.append(text)
        rprs.extend([_clean_rpr(run.find(qn("w:rPr")))] * len(text))
    return "".join(chars), rprs


def _group_by_rpr(tokens: list[str], rprs: list) -> list[tuple[str, object]]:
    """把连续同格式的 token 合并成一个 run 文本。"""
    groups: list[tuple[list[str], object]] = []
    keys: list[bytes] = []
    for token, rpr in zip(tokens, rprs):
        key = _rpr_key(rpr)
        if groups and keys[-1] == key:
            groups[-1][0].append(token)
        else:
            groups.append(([token], rpr))
            keys.append(key)
    return [("".join(parts), rpr) for parts, rpr in groups]


def _revision_text_run(text: str, rpr, deleted: bool = False):
    """生成一个带字体格式的 w:r，删除内容用 w:delText。"""
    import copy

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = OxmlElement("w:r")
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    node = OxmlElement("w:delText" if deleted else "w:t")
    node.set(qn("xml:space"), "preserve")
    node.text = text
    run.append(node)
    return run


def _track_paragraph_replacement(paragraph, new_text: str, rev_id: int,
                                 date: str) -> int:
    """在原文段落上写入逐词修订，只标记实际改动的片段，并保留行内格式。"""
    from docx.oxml import OxmlElement

    p = paragraph._p
    text_runs = _text_runs(p)
    old_text, char_rprs = _run_char_rprs(text_runs)
    rpr = char_rprs[0] if char_rprs else None

    nodes = []
    if not text_runs or not old_text:
        if new_text:
            ins = OxmlElement("w:ins")
            _set_revision_attrs(ins, rev_id, date)
            ins.append(_revision_text_run(new_text, rpr))
            nodes.append(ins)
            rev_id += 1
    else:
        old_tokens = _diff_tokens(old_text)
        old_rprs = []
        offset = 0
        for token in old_tokens:
            old_rprs.append(char_rprs[offset] if offset < len(char_rprs) else rpr)
            offset += len(token)
        new_tokens = _diff_tokens(new_text)
        matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for text, token_rpr in _group_by_rpr(
                        old_tokens[i1:i2], old_rprs[i1:i2]):
                    nodes.append(_revision_text_run(text, token_rpr))
                continue
            if i2 > i1:
                wrapper = OxmlElement("w:del")
                _set_revision_attrs(wrapper, rev_id, date)
                for text, token_rpr in _group_by_rpr(
                        old_tokens[i1:i2], old_rprs[i1:i2]):
                    wrapper.append(_revision_text_run(text, token_rpr, True))
                nodes.append(wrapper)
                rev_id += 1
            if j2 > j1:
                # 插入内容沿用插入点前的原文格式，避免整段套用首 run 格式
                anchor_rpr = old_rprs[i1 - 1] if i1 > 0 else old_rprs[0]
                wrapper = OxmlElement("w:ins")
                _set_revision_attrs(wrapper, rev_id, date)
                wrapper.append(_revision_text_run(
                    "".join(new_tokens[j1:j2]), anchor_rpr))
                nodes.append(wrapper)
                rev_id += 1

    if text_runs:
        anchor = text_runs[0]
        top = anchor
        while top.getparent() is not None and top.getparent() is not p:
            top = top.getparent()
        for node in nodes:
            top.addprevious(node)
        for run in text_runs:
            parent = run.getparent()
            if parent is not None:
                parent.remove(run)
    else:
        for node in nodes:
            p.append(node)
    return rev_id


def _mark_paragraph_mark_deleted(paragraph, rev_id: int, date: str) -> int:
    """整段删除时同时标记段落标记，接受修订后不残留空段。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    r_pr = p_pr.find(qn("w:rPr"))
    if r_pr is None:
        r_pr = OxmlElement("w:rPr")
        p_pr.append(r_pr)
    el = OxmlElement("w:del")
    _set_revision_attrs(el, rev_id, date)
    r_pr.append(el)
    return rev_id + 1


def _has_toc_field(doc) -> bool:
    from docx.oxml.ns import qn

    for node in doc.element.body.iter(qn("w:instrText")):
        if "TOC" in (node.text or "").upper():
            return True
    return False


def _enable_revision_settings(doc, update_fields: bool = False) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    settings = doc.settings.element
    if settings.find(qn("w:trackRevisions")) is None:
        settings.append(OxmlElement("w:trackRevisions"))
    if update_fields and settings.find(qn("w:updateFields")) is None:
        el = OxmlElement("w:updateFields")
        el.set(qn("w:val"), "true")
        settings.append(el)


def _apply_thesis_font(paragraph, font_body: str, font_latin: str, size: float,
                       bold: bool | None = None,
                       line_spacing: float | None = None,
                       space_before: float | None = None,
                       space_after: float | None = None,
                       first_line_chars: float | None = None,
                       page_break: bool | None = None) -> None:
    """仅在用户确认“按论文格式调整”时统一字体字号、行距与分页。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    for run in paragraph.runs:
        run.font.name = font_latin
        run.font.size = Pt(size)
        if bold is not None:
            run.bold = bold
        r_pr = run._element.get_or_add_rPr()
        fonts = r_pr.get_or_add_rFonts()
        fonts.set(qn("w:eastAsia"), font_body)
        fonts.set(qn("w:ascii"), font_latin)
        fonts.set(qn("w:hAnsi"), font_latin)

    pf = paragraph.paragraph_format
    if line_spacing is not None:
        pf.line_spacing = line_spacing
    if space_before is not None:
        pf.space_before = Pt(space_before)
    if space_after is not None:
        pf.space_after = Pt(space_after)
    if first_line_chars is not None:
        pf.first_line_indent = Pt(size * first_line_chars)
    if page_break is not None:
        pf.page_break_before = page_break


def _replace_paragraph_text(paragraph, new_text: str) -> None:
    """非修订导出：替换段落文字，保留段落样式、图片与原文行内字体格式。"""
    p = paragraph._p
    text_runs = _text_runs(p)
    char_rprs = _run_char_rprs(text_runs)[1]
    fallback = char_rprs[0] if char_rprs else None

    # 新文字按原文字符位置继承所属 run 的格式；新增字符沿用末尾格式
    runs: list[tuple[str, object]] = []
    buffer: list[str] = []
    current = None
    current_key = None
    for index, char in enumerate(new_text):
        rpr = char_rprs[min(index, len(char_rprs) - 1)] if char_rprs else fallback
        key = _rpr_key(rpr)
        if buffer and key == current_key:
            buffer.append(char)
        else:
            if buffer:
                runs.append(("".join(buffer), current))
            buffer = [char]
            current = rpr
            current_key = key
    if buffer:
        runs.append(("".join(buffer), current))

    new_runs = [_revision_text_run(text, rpr) for text, rpr in runs]

    if text_runs:
        anchor = text_runs[0]
        while anchor.getparent() is not p:
            anchor = anchor.getparent()
        for new_run in new_runs:
            anchor.addprevious(new_run)
        for run in text_runs:
            run.getparent().remove(run)
    else:
        for new_run in new_runs:
            p.append(new_run)


def _export_from_original(paper_id: str, path: str, paragraphs: list[dict],
                          requirement: dict | None, tracked: bool,
                          adjust_format: bool) -> BytesIO:
    """以原始 docx 为底稿写入修改：封面/目录/图片/表格与原有字体全部保留。

    tracked=True 写入 Word 修订；False 直接替换文字生成干净的修改稿。
    """
    from docx import Document

    fmt = _effective_format(requirement)
    font_body = str(fmt.get("font_body") or "宋体")
    font_latin = str(fmt.get("font_latin") or "Times New Roman")
    size = float(fmt.get("font_size_body_pt") or 12)
    line_spacing = float(fmt.get("line_spacing") or 1.5)
    space_before = float(fmt.get("space_before_pt") or 0)
    space_after = float(fmt.get("space_after_pt") or 0)
    indent_chars = float(fmt.get("first_line_indent_chars") or 0)
    page_break_heading = bool(fmt.get("page_break_before_heading"))

    doc = Document(path)
    try:
        base = store.load_version(paper_id, 0).get("paragraphs") or []
    except (FileNotFoundError, ValueError, OSError):
        base = []
    if not base:
        raise RuntimeError("缺少版本 0 基线，无法映射原文段落")
    base_map = {p.get("id"): p for p in base if p.get("id")}
    current_map = {p.get("id"): p for p in (paragraphs or []) if p.get("id")}
    entries = _docx_text_paragraphs(doc)
    _, entry_links = _align_base_paragraphs(base, entries)

    heading_changes: list[tuple[str, str]] = []
    for pid, current in current_map.items():
        previous = base_map.get(pid)
        if not previous or previous.get("type") != "heading":
            continue
        old_text = (previous.get("text") or "").strip()
        new_text = (current.get("text") or "").strip()
        if old_text and new_text and old_text != new_text:
            heading_changes.append((old_text, new_text))
    has_toc_field = _has_toc_field(doc)

    if adjust_format:
        for index, links in entry_links.items():
            kinds = {(base_map.get(pid) or {}).get("type") for pid, _ in links}
            if kinds & {"cover", "toc", "table", "table_note"}:
                continue
            is_heading = "heading" in kinds
            level = _heading_level(entries[index][0]) if is_heading else 0
            _apply_thesis_font(
                entries[index][0], font_body, font_latin, size,
                bold=True if is_heading else None,
                line_spacing=line_spacing,
                space_before=space_before,
                space_after=space_after,
                first_line_chars=0 if is_heading else indent_chars,
                page_break=True if (is_heading and level == 1 and page_break_heading) else None,
            )

    rev_date = datetime.datetime.now(datetime.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rev_id = 1
    for index, links in entry_links.items():
        paragraph, original_norm = entries[index]
        kinds = {(base_map.get(pid) or {}).get("type") for pid, _ in links}
        new_text = _entry_new_text(links, current_map)
        if "toc" in kinds and not has_toc_field:
            for old_heading, new_heading in heading_changes:
                if old_heading in new_text:
                    new_text = new_text.replace(old_heading, new_heading, 1)
        if _norm_text(new_text) == original_norm:
            continue
        if tracked:
            rev_id = _track_paragraph_replacement(
                paragraph, new_text.strip(), rev_id, rev_date
            )
            if not new_text.strip():
                rev_id = _mark_paragraph_mark_deleted(paragraph, rev_id, rev_date)
        elif new_text.strip():
            _replace_paragraph_text(paragraph, new_text.strip())
        else:
            parent = paragraph._p.getparent()
            if parent is not None:
                parent.remove(paragraph._p)

    if tracked:
        _enable_revision_settings(doc, update_fields=has_toc_field)
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    _save_export(paper_id, "tracked.docx" if tracked else "optimized.docx", buf)
    return buf


def export_docx(paper_id: str, paragraphs: list[dict], requirement: dict | None,
                tables: list[dict] | None = None, tracked: bool = False,
                format_mode: str = "auto") -> BytesIO:
    """生成论文正文 docx。

    上传的是 docx 且 format_mode 为 auto/original/thesis 时，直接在原文件上
    写入修改，保留封面、目录、图片、表格位置与原有字体；tracked=True 写 Word
    修订，False 生成干净的修改稿。"thesis" 会把正文字体统一为需求中的论文
    格式（仅在用户确认后使用）。PDF 论文仍按解析结果重新排版。
    """
    if format_mode in ("auto", "original", "thesis"):
        path = _original_docx_path(paper_id)
        if path:
            try:
                return _export_from_original(
                    paper_id, path, paragraphs, requirement,
                    tracked=tracked,
                    adjust_format=format_mode == "thesis",
                )
            except Exception as e:
                if format_mode == "original":
                    raise
                logger.warning("按原文导出 Word 失败，回退到重建排版: %s", e)

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    fmt = _effective_format(requirement)
    font_body = str(fmt.get("font_body") or "宋体")
    font_latin = str(fmt.get("font_latin") or "Times New Roman")
    size = float(fmt.get("font_size_body_pt") or 12)
    spacing = float(fmt.get("line_spacing") or 1.5)
    space_before = float(fmt.get("space_before_pt") or 0)
    space_after = float(fmt.get("space_after_pt") or 0)
    indent_chars = float(fmt.get("first_line_indent_chars") or 0)
    page_break_heading = bool(fmt.get("page_break_before_heading"))
    table_map = {
        t.get("paragraph_id"): t for t in (tables or []) if t.get("paragraph_id")
    }
    base_map: dict[str, dict] = {}
    if tracked:
        try:
            base_map = {
                p.get("id"): p
                for p in (store.load_version(paper_id, 0).get("paragraphs") or [])
            }
        except (FileNotFoundError, ValueError, OSError):
            base_map = {}

    doc = Document()
    normal = doc.styles["Normal"]
    _set_style_font(normal, font_latin, size, font_body)
    normal.paragraph_format.line_spacing = spacing
    normal.paragraph_format.space_before = Pt(space_before)
    normal.paragraph_format.space_after = Pt(space_after)

    heading_sizes = {1: 16, 2: 14, 3: 13, 4: 12}
    rev_date = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rev_id = 1
    if tracked:
        try:
            from docx.oxml import OxmlElement
            doc.settings.element.append(OxmlElement("w:trackRevisions"))
        except (AttributeError, ImportError):
            pass

    for p in paragraphs or []:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        ptype = p.get("type")
        old_text = None
        if tracked and p.get("id") in base_map:
            old_text = (base_map[p["id"]].get("text") or "").strip()
            if old_text == text:
                old_text = None
        if old_text:
            level = 0
            psize = size
            bold = False
            center = False
            indent = False
            if ptype == "heading":
                level = max(1, min(int(p.get("level") or 1), 4))
                psize = heading_sizes[level]
                bold = True
            elif ptype == "caption":
                center = True
            elif ptype not in ("cover", "toc", "table", "table_note") and not p.get("list_items"):
                indent = True
            _add_tracked_paragraph(
                doc, old_text, text, font_body, psize, spacing, rev_id, rev_date,
                bold=bold, first_indent=indent, center=center, heading_level=level,
            )
            rev_id += 2
            continue
        if ptype == "heading":
            level = int(p.get("level") or 1)
            level = max(1, min(level, 4))
            para = doc.add_paragraph(style=f"Heading {level}")
            para.paragraph_format.line_spacing = spacing
            para.paragraph_format.space_before = Pt(space_before)
            para.paragraph_format.space_after = Pt(space_after)
            para.paragraph_format.page_break_before = bool(
                level == 1 and page_break_heading
            )
            run = para.add_run(text)
            _set_run_font(run, font_latin, heading_sizes[level], bold=True,
                          east_asia=font_body)
        elif ptype == "caption":
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_latin, size, east_asia=font_body)
        elif ptype == "table":
            table = table_map.get(p.get("id"))
            if table:
                _add_docx_table(doc, table, font_latin, size, font_body)
            else:
                para = doc.add_paragraph()
                para.paragraph_format.line_spacing = spacing
                run = para.add_run(text)
                _set_run_font(run, font_latin, size, east_asia=font_body)
        elif ptype in ("cover", "toc"):
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_latin, size, east_asia=font_body)
        elif ptype == "table_note":
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_latin, max(9, size - 1.5), east_asia=font_body)
        elif p.get("list_items"):
            _add_list_docx(doc, text, font_latin, size, spacing, font_body)
        else:
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            if indent_chars:
                para.paragraph_format.first_line_indent = Pt(size * indent_chars)
            run = para.add_run(text)
            _set_run_font(run, font_latin, size, east_asia=font_body)

    if tracked and base_map:
        current_ids = {p.get("id") for p in (paragraphs or [])}
        for pid, base in base_map.items():
            if pid in current_ids:
                continue
            text = (base.get("text") or "").strip()
            if not text:
                continue
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            _add_revision_run(para, text, "del", font_body, size, False, rev_id, rev_date)
            rev_id += 1

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)

    _save_export(paper_id, "tracked.docx" if tracked else "optimized.docx", buf)
    return buf


_PDF_FONTS: dict[str, str] = {}

_BODY_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/Deng.ttf", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/simkai.ttf", 0),
]
_HEADING_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("C:/Windows/Fonts/simsunb.ttf", 0),
]


def _register_first_font(name: str, candidates: list[tuple[str, int]]) -> str:
    """按顺序尝试注册本机中文字体，返回可用的字体名。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, index in candidates:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
            return name
        except Exception:
            continue
    return ""


def _pdf_font_names() -> tuple[str, str]:
    """返回 (正文字体, 标题字体)，找不到本机字体时回退到内置 CID 宋体。"""
    if "body" in _PDF_FONTS:
        return _PDF_FONTS["body"], _PDF_FONTS["heading"]

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    body = _register_first_font("PaperBody", _BODY_FONT_CANDIDATES)
    heading = _register_first_font("PaperHeading", _HEADING_FONT_CANDIDATES)
    if not body:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            body = "STSong-Light"
        except Exception:
            body = "Helvetica"
    if not heading:
        heading = body
    _PDF_FONTS.update({"body": body, "heading": heading})
    return body, heading


def _add_pdf_table(story: list, table: dict, body_style, body_font: str,
                   size: float, page_width: float) -> None:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    rows = table.get("rows") or []
    if not rows:
        fallback = str(table.get("fallback_text") or "").strip()
        if fallback:
            story.append(Paragraph(escape(fallback), body_style))
        return
    cols = max(len(r) for r in rows)
    if cols <= 0:
        return
    cell_style = ParagraphStyle(
        "PaperTable",
        parent=body_style,
        fontSize=max(7, size - 2),
        leading=max(8, (size - 2) * 1.35),
        firstLineIndent=0,
        alignment=0,
        wordWrap="CJK",
    )
    data = []
    for row in rows:
        data.append([
            Paragraph(escape(str(row[j]) if j < len(row) else ""), cell_style)
            for j in range(cols)
        ])
    header_rows = max(0, int(table.get("header_rows") or 0))
    table_flow = Table(
        data,
        colWidths=[page_width / cols] * cols,
        repeatRows=min(header_rows, len(rows)),
    )
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    if header_rows:
        style.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.whitesmoke))
    table_flow.setStyle(TableStyle(style))
    story.append(table_flow)
    story.append(Spacer(1, 6))


def export_pdf(paper_id: str, paragraphs: list[dict], requirement: dict | None,
               tables: list[dict] | None = None) -> BytesIO:
    """生成论文正文 PDF：字体/字号/行距/标题层级/题注/首行缩进。"""
    try:
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as e:
        raise ValueError("reportlab 未安装，无法导出 PDF") from e

    fmt = _effective_format(requirement)
    size = float(fmt.get("font_size_body_pt") or 12)
    spacing = float(fmt.get("line_spacing") or 1.5)
    body_font, heading_font = _pdf_font_names()
    page_width = A4[0] - 144
    table_map = {
        t.get("paragraph_id"): t for t in (tables or []) if t.get("paragraph_id")
    }

    body_style = ParagraphStyle(
        "PaperBody",
        fontName=body_font,
        fontSize=size,
        leading=size * spacing * 1.2,
        firstLineIndent=size * 2,
        alignment=TA_JUSTIFY,
        wordWrap="CJK",
        spaceAfter=0,
    )
    plain_style = ParagraphStyle(
        "PaperPlain",
        parent=body_style,
        firstLineIndent=0,
    )
    caption_style = ParagraphStyle(
        "PaperCaption",
        parent=body_style,
        firstLineIndent=0,
        alignment=TA_CENTER,
    )
    note_style = ParagraphStyle(
        "PaperTableNote",
        parent=body_style,
        fontSize=max(9, size - 1.5),
        leading=max(10, (size - 1.5) * spacing * 1.2),
        firstLineIndent=0,
    )
    heading_sizes = {1: 16, 2: 14, 3: 13, 4: 12}
    heading_styles = {
        level: ParagraphStyle(
            f"PaperHeading{level}",
            fontName=heading_font,
            fontSize=heading_sizes[level],
            leading=heading_sizes[level] * 1.4,
            spaceBefore=12 if level == 1 else 9,
            spaceAfter=6,
            wordWrap="CJK",
        )
        for level in heading_sizes
    }

    story = []
    for p in paragraphs or []:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        ptype = p.get("type")
        if ptype == "heading":
            level = max(1, min(int(p.get("level") or 1), 4))
            story.append(Paragraph(escape(text), heading_styles[level]))
        elif ptype == "caption":
            story.append(Paragraph(escape(text), caption_style))
        elif ptype == "table":
            table = table_map.get(p.get("id"))
            if table:
                _add_pdf_table(story, table, body_style, body_font, size, page_width)
            else:
                story.append(Paragraph(escape(text), body_style))
        elif ptype in ("cover", "toc"):
            story.append(Paragraph(escape(text), plain_style))
        elif ptype == "table_note":
            story.append(Paragraph(escape(text), note_style))
        elif p.get("list_items"):
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    story.append(Paragraph(escape(line), body_style))
        else:
            story.append(Paragraph(escape(text), body_style))

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=72,
        rightMargin=72,
        topMargin=72,
        bottomMargin=72,
        title="优化后的论文",
    )
    doc.build(story or [Spacer(1, 1)])
    buf.seek(0)

    _save_export(paper_id, "optimized.pdf", buf)
    return buf


_SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _add_summary_heading(doc, text: str, level: int = 1) -> None:
    from docx.shared import Pt

    sizes = {1: 15, 2: 13, 3: 12}
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    para.paragraph_format.space_after = Pt(4)
    run = para.add_run(text)
    _set_run_font(run, "黑体", sizes.get(level, 12), bold=True)


def export_summary_docx(paper_id: str, meta: dict, parsed: dict,
                        requirement: dict | None, analyses: dict | None) -> BytesIO:
    """导出《论文修改说明》：诊断结论、问题清单与段落修改对照。"""
    from docx import Document
    from docx.shared import Pt

    analyses = analyses or {}
    doc = Document()
    normal = doc.styles["Normal"]
    _set_style_font(normal, "宋体", 10.5)
    normal.paragraph_format.line_spacing = 1.4
    normal.paragraph_format.space_after = Pt(2)

    title = doc.add_paragraph()
    title.alignment = 1
    run = title.add_run("论文修改说明")
    _set_run_font(run, "黑体", 18, bold=True)

    info = [
        ("论文标题", meta.get("title") or parsed.get("title") or "未命名论文"),
        ("原文件", meta.get("filename") or "-"),
        ("当前版本", f"v{meta.get('current_version', 0)}"),
        ("导出时间", datetime.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    for key, value in info:
        para = doc.add_paragraph()
        r1 = para.add_run(f"{key}：")
        _set_run_font(r1, "黑体", 10.5, bold=True)
        r2 = para.add_run(str(value))
        _set_run_font(r2, "宋体", 10.5)

    _add_summary_heading(doc, "一、诊断结论", 1)
    if analyses:
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("维度", "得分", "结论")):
            cell.text = ""
            run = cell.paragraphs[0].add_run(text)
            _set_run_font(run, "黑体", 10, bold=True)
        for dim, data in analyses.items():
            cells = table.add_row().cells
            label = (DIAGNOSIS_DIMENSIONS.get(dim) or {}).get("label") or dim
            score = data.get("score")
            values = (label, "-" if score is None else str(score),
                      str(data.get("summary") or ""))
            for cell, text in zip(cells, values):
                cell.text = ""
                run = cell.paragraphs[0].add_run(text)
                _set_run_font(run, "宋体", 9.5)
    else:
        doc.add_paragraph("尚未完成诊断。")

    _add_summary_heading(doc, "二、问题清单", 1)
    issue_count = 0
    for dim, data in analyses.items():
        for issue in data.get("issues") or []:
            issue_count += 1
            label = (DIAGNOSIS_DIMENSIONS.get(dim) or {}).get("label") or dim
            sev = _SEVERITY_LABELS.get(str(issue.get("severity") or "medium"), "中")
            para = doc.add_paragraph()
            run = para.add_run(
                f"[{label} · {sev}] "
                f"{issue.get('issue') or issue.get('rule_id') or '问题'}"
            )
            _set_run_font(run, "宋体", 10.5, bold=True)
            if issue.get("evidence"):
                p = doc.add_paragraph()
                r = p.add_run(f"证据：{issue['evidence']}")
                _set_run_font(r, "宋体", 9.5)
            if issue.get("suggestion"):
                p = doc.add_paragraph()
                r = p.add_run(f"建议：{issue['suggestion']}")
                _set_run_font(r, "宋体", 9.5)
    if not issue_count:
        doc.add_paragraph("暂无诊断问题记录。")

    _add_summary_heading(doc, "三、段落修改对照", 1)
    try:
        base = store.load_version(paper_id, 0).get("paragraphs") or []
    except FileNotFoundError:
        base = parsed.get("paragraphs") or []
    current = store.current_paragraphs(paper_id)
    current_map = {p.get("id"): p for p in current}
    changes = [p for p in base if (current_map.get(p.get("id")) or {}).get("text")
               and (current_map[p["id"]].get("text") or "").strip()
               != (p.get("text") or "").strip()]
    if not changes:
        doc.add_paragraph("尚未采纳任何段落改写。")
    for p in changes:
        new = current_map[p.get("id")]
        para = doc.add_paragraph()
        run = para.add_run(
            f"{p.get('id')} · 第 {p.get('page') or '-'} 页"
            + (f" · {p.get('section')}" if p.get("section") else "")
        )
        _set_run_font(run, "黑体", 10.5, bold=True)
        old_p = doc.add_paragraph()
        r = old_p.add_run(f"原文：{p.get('text') or ''}")
        _set_run_font(r, "宋体", 9.5)
        new_p = doc.add_paragraph()
        r = new_p.add_run(f"修改后：{new.get('text') or ''}")
        _set_run_font(r, "宋体", 9.5)

    note = doc.add_paragraph()
    r = note.add_run(
        "说明：本报告仅记录平台内采纳的段落改写与规则/模型诊断结果，"
        "不包含平台外的修改；AI 率与诊断分数均为辅助参考。"
    )
    _set_run_font(r, "宋体", 9)

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    _save_export(paper_id, "modification_summary.docx", buf)
    return buf
