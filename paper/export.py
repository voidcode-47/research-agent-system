"""按需求格式导出 Word（python-docx）。

中文字体必须通过 qn('w:eastAsia') 设置，否则 Word 中不生效。
"""
import os
from io import BytesIO

from config.paper import DEFAULT_FORMAT
from paper import store


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

    # 同步落一份 exports/optimized.docx
    try:
        out_dir = os.path.join(store.paper_dir(paper_id), "exports")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "optimized.docx"), "wb") as f:
            f.write(buf.getvalue())
        buf.seek(0)
    except OSError:
        pass

    return buf
