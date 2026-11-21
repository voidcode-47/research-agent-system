"""按需求格式导出 Word（python-docx）/ PDF（reportlab）。

中文字体必须通过 qn('w:eastAsia') 设置，否则 Word 中不生效。
"""
import datetime
import os
from io import BytesIO
from xml.sax.saxutils import escape

from config.paper import (
    DEFAULT_FORMAT,
    DIAGNOSIS_DIMENSIONS,
    TEMPLATE_FORMAT_DEFAULTS,
)
from paper import store
from utils.atomic_file import atomic_write


def _effective_format(requirement: dict | None) -> dict:
    """合并需求格式与模板默认值；仍是通用默认值的字段按所选模板排版。"""
    user = dict(((requirement or {}).get("format")) or {})
    template = user.get("template") or DEFAULT_FORMAT["template"]
    overrides = TEMPLATE_FORMAT_DEFAULTS.get(template) or {}
    fmt = dict(DEFAULT_FORMAT)
    fmt.update(overrides)
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


def _set_run_font(run, font_name: str, size_pt: float, bold: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), font_name)


def _set_style_font(style, font_name: str, size_pt: float) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    style.font.name = font_name
    style.font.size = Pt(size_pt)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), font_name)


def _add_docx_table(doc, table: dict, font_name: str, size: float) -> None:
    rows = table.get("rows") or []
    if not rows:
        fallback = str(table.get("fallback_text") or "").strip()
        if fallback:
            para = doc.add_paragraph()
            run = para.add_run(fallback)
            _set_run_font(run, font_name, size)
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
            _set_run_font(run, font_name, max(8, size - 1), bold=i < header_rows)


def _add_list_docx(doc, text: str, font_name: str, size: float, spacing: float) -> None:
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
        _set_run_font(run, font_name, size)


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


def export_docx(paper_id: str, paragraphs: list[dict], requirement: dict | None,
                tables: list[dict] | None = None, tracked: bool = False) -> BytesIO:
    """生成论文正文 docx：字体/字号/行距/标题层级/题注/首行缩进。

    tracked=True 时以版本 0 为基线，把改动写成 Word 修订（插入/删除），
    并在文档设置中开启修订模式，便于作者逐条接受或拒绝。
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    fmt = _effective_format(requirement)
    font_body = str(fmt.get("font_body") or "宋体")
    size = float(fmt.get("font_size_body_pt") or 12)
    spacing = float(fmt.get("line_spacing") or 1.5)
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
    _set_style_font(normal, font_body, size)
    normal.paragraph_format.line_spacing = spacing
    normal.paragraph_format.space_after = Pt(0)

    heading_sizes = {1: 16, 2: 14, 3: 13, 4: 12}
    rev_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            run = para.add_run(text)
            _set_run_font(run, font_body, heading_sizes[level], bold=True)
        elif ptype == "caption":
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_body, size)
        elif ptype == "table":
            table = table_map.get(p.get("id"))
            if table:
                _add_docx_table(doc, table, font_body, size)
            else:
                para = doc.add_paragraph()
                para.paragraph_format.line_spacing = spacing
                run = para.add_run(text)
                _set_run_font(run, font_body, size)
        elif ptype in ("cover", "toc"):
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_body, size)
        elif ptype == "table_note":
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            run = para.add_run(text)
            _set_run_font(run, font_body, max(9, size - 1.5))
        elif p.get("list_items"):
            _add_list_docx(doc, text, font_body, size, spacing)
        else:
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            para.paragraph_format.first_line_indent = Pt(size * 2)  # 首行缩进 2 字符
            run = para.add_run(text)
            _set_run_font(run, font_body, size)

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
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

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
