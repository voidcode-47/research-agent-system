# -*- coding: utf-8 -*-
"""PDF 校验与结构解析。"""
import pytest

from paper import parser
from paper.parser import PDFValidationError, parse_pdf, validate_pdf

fitz = pytest.importorskip("fitz")


SAMPLE_LINES = [
    "Deep Learning for Software Testing",
    "",
    "Abstract",
    "This paper reviews deep learning methods for software testing. "
    "We summarize fifty studies and compare their datasets and metrics.",
    "Keywords: deep learning, software testing, survey",
    "",
    "1 Introduction",
    "Software testing is important. Deep learning has been applied to test generation.",
    "2 Method",
    "We build a taxonomy of existing work. The method compares datasets and metrics.",
    "",
    "References",
    "[1] Smith J. A survey of deep learning for testing[J]. Journal of Software, 2020, 12(3): 1-10.",
]


def _make_pdf(path, lines=None, pages=1, title="Testing Paper"):
    doc = fitz.open()
    doc.set_metadata({"title": title})
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for line in (lines if lines is not None else SAMPLE_LINES):
            if line:
                page.insert_text((72, y), line, fontname="helv", fontsize=10)
            y += 14
    doc.save(str(path))
    doc.close()
    return path


# ---------- 校验 ----------
def test_validate_accepts_pdf():
    validate_pdf("paper.PDF", b"%PDF-1.7\n...", "application/pdf")


@pytest.mark.parametrize("name,head,ctype,msg", [
    ("paper.txt", b"%PDF-1.7", "text/plain", "仅支持 PDF"),
    ("paper.pdf", b"not a pdf", "application/pdf", "文件头"),
    ("paper.pdf", b"%PDF-1.7", "text/plain", "Content-Type"),
])
def test_validate_rejects(name, head, ctype, msg):
    with pytest.raises(PDFValidationError) as ei:
        validate_pdf(name, head, ctype)
    assert msg in str(ei.value)


def test_validate_without_content_type():
    validate_pdf("a.pdf", b"%PDF-1.4")


# ---------- 解析 ----------
def test_parse_structure(tmp_path):
    path = _make_pdf(tmp_path / "paper.pdf")
    parsed = parse_pdf(str(path))
    assert parsed["title"] == "Testing Paper"
    assert parsed["pages"] == 1
    assert parsed["text_chars"] > 100

    titles = [s["title"] for s in parsed["sections"]]
    assert "Abstract" in titles
    assert "1 Introduction" in titles
    assert "2 Method" in titles
    assert "References" in titles

    types = {p["type"] for p in parsed["paragraphs"]}
    assert {"heading", "body", "reference"} <= types
    assert parsed["abstract"].startswith("This paper reviews")
    assert "deep" in parsed["keywords"]
    assert len(parsed["keywords"]) >= 3

    refs = [p for p in parsed["paragraphs"] if p["type"] == "reference"]
    assert refs and refs[0]["text"].startswith("[1]")
    # 段落 id 连续编号
    assert parsed["paragraphs"][0]["id"] == "p_0001"


def test_parse_encrypted_pdf_rejected(tmp_path):
    path = tmp_path / "locked.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret content " * 20, fontname="helv", fontsize=10)
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()
    with pytest.raises(ValueError) as ei:
        parse_pdf(str(path))
    assert "加密" in str(ei.value)


def test_parse_scanned_pdf_rejected(tmp_path):
    path = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    with pytest.raises(ValueError) as ei:
        parse_pdf(str(path))
    assert "OCR" in str(ei.value)


def test_parse_page_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "MAX_PAGES", 1)
    path = _make_pdf(tmp_path / "many.pdf", pages=2)
    with pytest.raises(ValueError) as ei:
        parse_pdf(str(path))
    assert "超过 1 页上限" in str(ei.value)


def test_parse_invalid_file(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(ValueError) as ei:
        parse_pdf(str(path))
    assert "PDF 打开失败" in str(ei.value)
