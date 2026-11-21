# -*- coding: utf-8 -*-
"""导出：列表块还原、封面/目录保留、原生表格不压成正文。"""
import io

import pytest

import paper.export as export

docx = pytest.importorskip("docx")
fitz = pytest.importorskip("fitz")


def _sample():
    paragraphs = [
        {"id": "p_0001", "type": "cover", "text": "Cover Title", "page": 1},
        {"id": "p_0002", "type": "toc", "text": "Contents", "page": 1},
        {"id": "p_0003", "type": "heading", "level": 1, "text": "1 Method", "page": 2},
        {"id": "p_0004", "type": "body", "text": "（1）第一项。\n（2）第二项。",
         "list_items": 2, "page": 2},
        {"id": "p_0005", "type": "table", "text": "A | B", "page": 2},
        {"id": "p_0006", "type": "body", "text": "After table.", "page": 2},
    ]
    tables = [{
        "id": "t_0001",
        "paragraph_id": "p_0005",
        "rows": [["A", "B"], ["1", "2"]],
        "header_rows": 1,
        "fallback_text": "",
    }]
    return paragraphs, tables


def test_export_docx_keeps_native_table_and_list_items(monkeypatch):
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    paragraphs, tables = _sample()
    buf = export.export_docx("p_test", paragraphs, None, tables)
    doc = docx.Document(io.BytesIO(buf.getvalue()))
    texts = [p.text for p in doc.paragraphs]
    assert "Cover Title" in texts and "Contents" in texts
    assert "（1）第一项。" in texts and "（2）第二项。" in texts
    assert "A | B" not in texts
    assert len(doc.tables) == 1
    assert [[cell.text for cell in row.cells] for row in doc.tables[0].rows] == [
        ["A", "B"], ["1", "2"],
    ]


def test_export_docx_applies_apa_template_typography(monkeypatch):
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    paragraphs, tables = _sample()
    buf = export.export_docx(
        "p_test", paragraphs, {"format": {"template": "apa"}}, tables,
    )
    doc = docx.Document(io.BytesIO(buf.getvalue()))
    normal = doc.styles["Normal"]
    assert normal.font.name == "Times New Roman"
    assert normal.paragraph_format.line_spacing == 2.0


def _track_text(paragraph, accept):
    """按接受/拒绝修订后的结果还原段落文字。"""
    from docx.oxml.ns import qn

    parts = []
    for child in paragraph._p:
        tag = child.tag.split("}")[-1]
        if tag == "del":
            if not accept:
                parts.extend(n.text or "" for n in child.iter(qn("w:delText")))
        elif tag == "ins":
            if accept:
                parts.extend(n.text or "" for n in child.iter(qn("w:t")))
        else:
            parts.extend(n.text or "" for n in child.iter(qn("w:t")))
    return "".join(parts)


def test_export_docx_tracked_writes_revisions(monkeypatch):
    from docx.oxml.ns import qn

    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    base = [
        {"id": "p1", "type": "body", "text": "旧内容。", "page": 1},
        {"id": "p2", "type": "body", "text": "保留内容。", "page": 1},
        {"id": "p3", "type": "body", "text": "将被删除。", "page": 1},
    ]
    current = [
        {"id": "p1", "type": "body", "text": "新内容。", "page": 1},
        {"id": "p2", "type": "body", "text": "保留内容。", "page": 1},
    ]
    monkeypatch.setattr(
        export.store, "load_version", lambda pid, version: {"paragraphs": base},
    )
    buf = export.export_docx("p_test", current, None, None, tracked=True)
    doc = docx.Document(io.BytesIO(buf.getvalue()))
    xml = doc.element.xml
    assert "<w:ins" in xml and "<w:del" in xml
    accepted = [_track_text(p, True) for p in doc.paragraphs]
    rejected = [_track_text(p, False) for p in doc.paragraphs]
    assert "新内容。" in accepted and "旧内容。" in rejected
    assert "将被删除。" in rejected
    assert doc.settings.element.find(qn("w:trackRevisions")) is not None


def test_export_pdf_keeps_native_table(monkeypatch):
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    paragraphs, tables = _sample()
    buf = export.export_pdf("p_test", paragraphs, None, tables)
    doc = fitz.open("pdf", buf.getvalue())
    text = "".join(page.get_text() for page in doc)
    assert "Cover Title" in text and "Contents" in text
    assert "A" in text and "B" in text
    assert "A | B" not in text


# ---------- 按原 docx 导出修订版 ----------
_ORIGINAL_FIRST = "这是第一段正文，用于测试原文字体和版式是否被完整保留。"
_ORIGINAL_SECOND = "这是第二段正文，导出修订版时不应发生任何变化。"
_REVISED_FIRST = "这是第一段正文，已按原文格式改写并保留原有版式。"


def _make_original_docx(path):
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = docx.Document()
    doc.add_paragraph("封面标题")
    for text in (_ORIGINAL_FIRST, _ORIGINAL_SECOND):
        para = doc.add_paragraph()
        run = para.add_run(text)
        run.font.name = "仿宋"
        run.font.size = Pt(10.5)
        rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
        rfonts.set(qn("w:eastAsia"), "仿宋")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "A"
    table.rows[0].cells[1].text = "B"
    doc.save(str(path))
    return path


def _original_base():
    return [
        {"id": "p_0001", "type": "cover", "text": "封面标题", "page": 1},
        {"id": "p_0002", "type": "body", "text": _ORIGINAL_FIRST, "page": 2},
        {"id": "p_0003", "type": "body", "text": _ORIGINAL_SECOND, "page": 2},
        {"id": "p_0004", "type": "table", "text": "A | B", "page": 2},
    ]


def test_tracked_export_preserves_original_docx_layout(monkeypatch, tmp_path):
    from docx.oxml.ns import qn

    path = _make_original_docx(tmp_path / "original.docx")
    base = _original_base()
    current = [dict(p) for p in base]
    current[1]["text"] = _REVISED_FIRST
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: str(path))
    monkeypatch.setattr(
        export.store, "load_version", lambda pid, n: {"paragraphs": base},
    )

    buf = export.export_docx("p_test", current, None, None, tracked=True)
    doc = docx.Document(io.BytesIO(buf.getvalue()))

    # 首页、表格与未修改段落保持原样
    assert doc.paragraphs[0].text == "封面标题"
    assert len(doc.tables) == 1
    assert "<w:ins" not in doc.paragraphs[0]._p.xml
    assert "<w:del" not in doc.paragraphs[0]._p.xml
    assert any(p.text == _ORIGINAL_SECOND for p in doc.paragraphs)

    # 改写段落写入逐词修订，接受/拒绝修订后分别得到新文与原文
    revised_para = next(p for p in doc.paragraphs
                        if _track_text(p, True) == _REVISED_FIRST
                        and _track_text(p, False) == _ORIGINAL_FIRST)
    assert revised_para is not None
    ins = next(i for i in doc.element.body.iter(qn("w:ins")))
    ins_run = ins.find(qn("w:r"))
    rfonts = ins_run.find(qn("w:rPr")).find(qn("w:rFonts"))
    assert rfonts.get(qn("w:eastAsia")) == "仿宋"


def test_docx_export_without_tracking_preserves_original_layout(monkeypatch, tmp_path):
    from docx.oxml.ns import qn

    path = _make_original_docx(tmp_path / "original.docx")
    base = _original_base()
    current = [dict(p) for p in base]
    current[1]["text"] = _REVISED_FIRST
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: str(path))
    monkeypatch.setattr(
        export.store, "load_version", lambda pid, n: {"paragraphs": base},
    )

    buf = export.export_docx("p_test", current, None, None, tracked=False)
    doc = docx.Document(io.BytesIO(buf.getvalue()))

    # 第一页、表格与未修改段落保持原样，且不留下修订标记
    assert doc.paragraphs[0].text == "封面标题"
    assert len(doc.tables) == 1
    assert "<w:ins" not in doc.element.xml
    assert "<w:del" not in doc.element.xml
    assert any(p.text == _ORIGINAL_SECOND for p in doc.paragraphs)

    # 改写段落沿用原文 run 的字体
    changed = next(p for p in doc.paragraphs if p.text == _REVISED_FIRST)
    rfonts = changed.runs[0]._element.rPr.rFonts
    assert rfonts.get(qn("w:eastAsia")) == "仿宋"


def test_inspect_original_format_reports_font_mismatch(monkeypatch, tmp_path):
    path = _make_original_docx(tmp_path / "original.docx")
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: str(path))

    report = export.inspect_original_format(
        "p_test", {"format": {"template": "generic_thesis"}}
    )
    assert report["has_original_docx"] is True
    assert report["compliant"] is False
    assert any("仿宋" in issue for issue in report["issues"])

    ok = export.inspect_original_format("p_test", {"format": {
        "font_body": "仿宋", "font_size_body_pt": 10.5,
    }})
    assert ok["compliant"] is True


def test_inspect_original_format_without_docx(monkeypatch):
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: None)
    report = export.inspect_original_format("p_test", None)
    assert report["has_original_docx"] is False
    assert report["compliant"] is True


def test_effective_format_applies_template_defaults_without_explicit():
    fmt = export._effective_format(
        {"format": {"template": "ieee", "font_size_body_pt": 12}}
    )
    assert fmt["font_size_body_pt"] == 10
    assert fmt["line_spacing"] == 1.0


def test_effective_format_respects_explicit_form_values():
    fmt = export._effective_format({"format": {
        "template": "ieee", "explicit": True,
        "font_body": "宋体", "font_latin": "Times New Roman",
        "font_size_body_pt": 12, "line_spacing": 1.5,
        "space_before_pt": 0, "space_after_pt": 0,
        "first_line_indent_chars": 2, "page_break_before_heading": True,
    }})
    assert fmt["font_size_body_pt"] == 12
    assert fmt["line_spacing"] == 1.5
    assert fmt["page_break_before_heading"] is True


_MIXED = "关键词：大语言模型；检索增强生成。"
_MIXED_REVISED = "关键词：大语言模型、检索增强生成。"


def _make_mixed_format_docx(path):
    doc = docx.Document()
    para = doc.add_paragraph()
    para.add_run("关键词：").bold = True
    para.add_run("大语言模型；检索增强生成。")
    doc.save(str(path))
    return path


def _mixed_base():
    return [{"id": "p_0001", "type": "body", "text": _MIXED, "page": 1}]


def _run_bold_map(doc):
    from docx.oxml.ns import qn

    out = []
    for run in doc.element.body.iter(qn("w:r")):
        text = "".join(n.text or "" for n in run.iter(qn("w:t")))
        text += "".join(n.text or "" for n in run.iter(qn("w:delText")))
        if not text:
            continue
        rpr = run.find(qn("w:rPr"))
        out.append((text, rpr is not None and rpr.find(qn("w:b")) is not None))
    return out


def test_tracked_export_keeps_inline_run_formatting(monkeypatch, tmp_path):
    path = _make_mixed_format_docx(tmp_path / "mixed.docx")
    base = _mixed_base()
    current = [dict(base[0], text=_MIXED_REVISED)]
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: str(path))
    monkeypatch.setattr(
        export.store, "load_version", lambda pid, n: {"paragraphs": base},
    )

    buf = export.export_docx("p_test", current, None, None, tracked=True)
    doc = docx.Document(io.BytesIO(buf.getvalue()))
    assert _track_text(doc.paragraphs[0], True) == _MIXED_REVISED
    assert _track_text(doc.paragraphs[0], False) == _MIXED

    bold = dict(_run_bold_map(doc))
    assert bold["关键词："] is True
    assert all(not is_bold for text, is_bold in bold.items() if "大语言模型" in text)
    assert all(not is_bold for text, is_bold in bold.items() if "检索增强生成" in text)


def test_clean_export_keeps_inline_run_formatting(monkeypatch, tmp_path):
    path = _make_mixed_format_docx(tmp_path / "mixed.docx")
    base = _mixed_base()
    current = [dict(base[0], text=_MIXED_REVISED)]
    monkeypatch.setattr(export, "_save_export", lambda *a, **k: None)
    monkeypatch.setattr(export, "_original_docx_path", lambda pid: str(path))
    monkeypatch.setattr(
        export.store, "load_version", lambda pid, n: {"paragraphs": base},
    )

    buf = export.export_docx("p_test", current, None, None, tracked=False)
    doc = docx.Document(io.BytesIO(buf.getvalue()))
    assert doc.paragraphs[0].text == _MIXED_REVISED
    bold = dict(_run_bold_map(doc))
    assert bold["关键词："] is True
    assert all(not is_bold for text, is_bold in bold.items() if "大语言模型" in text)

