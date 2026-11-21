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
    assert "新内容。" in xml and "旧内容。" in xml and "将被删除。" in xml
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
