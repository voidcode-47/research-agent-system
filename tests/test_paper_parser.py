# -*- coding: utf-8 -*-
"""PDF / Word 校验与结构解析。"""
import pytest

from paper import parser
from paper.parser import (
    PDFValidationError, UploadValidationError, parse_docx, parse_pdf,
    validate_pdf, validate_upload,
)

fitz = pytest.importorskip("fitz")
docx = pytest.importorskip("docx")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


def test_validate_upload_accepts_pdf_and_docx():
    assert validate_upload("a.PDF", b"%PDF-1.7", "application/pdf") == "pdf"
    assert validate_upload("a.docx", b"PK\x03\x04rest", DOCX_MIME) == "docx"
    assert validate_upload("a.docx", b"PK\x03\x04rest", "application/octet-stream") == "docx"


@pytest.mark.parametrize("name,head,ctype,msg", [
    ("a.doc", b"PK\x03\x04", "application/msword", "旧版 .doc"),
    ("a.txt", b"hello", "text/plain", "仅支持 PDF"),
    ("a.docx", b"not-a-zip", DOCX_MIME, "ZIP 文件头"),
    ("a.docx", b"PK\x03\x04rest", "text/plain", "Content-Type"),
])
def test_validate_upload_rejects(name, head, ctype, msg):
    with pytest.raises(UploadValidationError) as ei:
        validate_upload(name, head, ctype)
    assert msg in str(ei.value)


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


# ---------- Word 解析 ----------
def _make_docx(path, title=""):
    document = docx.Document()
    document.core_properties.title = title
    document.add_paragraph("基于深度学习的人脸识别研究")
    document.add_paragraph("摘要", style="Heading 1")
    document.add_paragraph(
        "本文研究了人脸识别中的特征提取方法，提出了一种改进的卷积神经网络结构，"
        "并在公开数据集上验证了方法的有效性，实验结果表明准确率有明显提升。"
    )
    document.add_paragraph("关键词：人脸识别；深度学习；卷积神经网络")
    document.add_paragraph("1 绪论", style="Heading 1")
    document.add_paragraph("近年来，人脸识别技术发展迅速，被广泛应用于身份认证等领域。")
    document.add_paragraph("1.1 研究背景", style="Heading 2")
    document.add_paragraph("现有方法在光照变化和遮挡场景下仍然存在不足，需要进一步改进。")
    document.add_paragraph("图 1 网络结构示意图")
    document.add_paragraph("参考文献", style="Heading 1")
    document.add_paragraph("[1] 张三. 人脸识别研究[J]. 计算机学报, 2020, 43(5): 1-10.")
    document.save(str(path))
    return path


def test_parse_docx_structure(tmp_path):
    path = _make_docx(tmp_path / "paper.docx")
    parsed = parse_docx(str(path))
    assert parsed["title"] == "基于深度学习的人脸识别研究"
    assert parsed["pages"] >= 1
    assert parsed["text_chars"] >= parser.MIN_TEXT_CHARS

    titles = [(s["level"], s["title"]) for s in parsed["sections"]]
    assert (1, "摘要") in titles
    assert (1, "1 绪论") in titles
    assert (2, "1.1 研究背景") in titles
    assert (1, "参考文献") in titles

    types = {p["type"] for p in parsed["paragraphs"]}
    assert {"heading", "body", "caption", "reference"} <= types
    assert "人脸识别" in parsed["keywords"]
    assert parsed["abstract"].startswith("本文研究了人脸识别")
    assert parsed["paragraphs"][-1]["text"].startswith("[1]")


def test_parse_docx_uses_core_title(tmp_path):
    path = _make_docx(tmp_path / "paper.docx", title="核心属性标题")
    assert parse_docx(str(path))["title"] == "核心属性标题"


def test_parse_docx_title_falls_back_to_upload_name(tmp_path):
    document = docx.Document()
    document.add_paragraph("这是一段没有标题的正文，" * 20)
    path = tmp_path / "original.docx"
    document.save(str(path))
    parsed = parse_docx(str(path), "我的论文.docx")
    assert parsed["title"] == "我的论文"
    assert parse_docx(str(path))["title"] == "original"


def test_parse_docx_empty_rejected(tmp_path):
    document = docx.Document()
    document.add_paragraph("太短")
    path = tmp_path / "empty.docx"
    document.save(str(path))
    with pytest.raises(ValueError) as ei:
        parse_docx(str(path))
    assert "有效文本" in str(ei.value)


def test_parse_docx_invalid_file(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"PK\x03\x04 not really a docx")
    with pytest.raises(ValueError) as ei:
        parse_docx(str(path))
    assert "Word 打开失败" in str(ei.value)
