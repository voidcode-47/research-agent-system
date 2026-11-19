"""按需求格式导出 Word（python-docx）/ PDF（reportlab）。

中文字体必须通过 qn('w:eastAsia') 设置，否则 Word 中不生效。
"""
import datetime
import os
from io import BytesIO
from xml.sax.saxutils import escape

from config.paper import DEFAULT_FORMAT, DIAGNOSIS_DIMENSIONS
from paper import store


def _save_export(paper_id: str, filename: str, buf: BytesIO) -> None:
    """尽力保存导出副本；磁盘不可写时仍可下载内存中的结果。"""
    try:
        out_dir = os.path.join(store.paper_dir(paper_id), "exports")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, filename), "wb") as f:
            f.write(buf.getvalue())
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


def export_docx(paper_id: str, paragraphs: list[dict], requirement: dict | None) -> BytesIO:
    """生成论文正文 docx：字体/字号/行距/标题层级/题注/首行缩进。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    fmt = dict(DEFAULT_FORMAT)
    fmt.update((requirement or {}).get("format") or {})
    font_body = str(fmt.get("font_body") or "宋体")
    size = float(fmt.get("font_size_body_pt") or 12)
    spacing = float(fmt.get("line_spacing") or 1.5)

    doc = Document()
    normal = doc.styles["Normal"]
    _set_style_font(normal, font_body, size)
    normal.paragraph_format.line_spacing = spacing
    normal.paragraph_format.space_after = Pt(0)

    heading_sizes = {1: 16, 2: 14, 3: 13, 4: 12}

    for p in paragraphs or []:
        text = (p.get("text") or "").strip()
        if not text:
            continue
        ptype = p.get("type")
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
        else:
            para = doc.add_paragraph()
            para.paragraph_format.line_spacing = spacing
            para.paragraph_format.first_line_indent = Pt(size * 2)  # 首行缩进 2 字符
            run = para.add_run(text)
            _set_run_font(run, font_body, size)

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)

    _save_export(paper_id, "optimized.docx", buf)
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


def export_pdf(paper_id: str, paragraphs: list[dict], requirement: dict | None) -> BytesIO:
    """生成论文正文 PDF：字体/字号/行距/标题层级/题注/首行缩进。"""
    try:
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as e:
        raise ValueError("reportlab 未安装，无法导出 PDF") from e

    fmt = dict(DEFAULT_FORMAT)
    fmt.update((requirement or {}).get("format") or {})
    size = float(fmt.get("font_size_body_pt") or 12)
    spacing = float(fmt.get("line_spacing") or 1.5)
    body_font, heading_font = _pdf_font_names()

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
    caption_style = ParagraphStyle(
        "PaperCaption",
        parent=body_style,
        firstLineIndent=0,
        alignment=TA_CENTER,
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
