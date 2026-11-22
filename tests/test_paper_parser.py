"""PDF / Word 校验与结构解析。"""
import pytest

from paper import parser
from paper.parser import (
    PDFValidationError,
    UploadValidationError,
    parse_docx,
    parse_pdf,
    validate_pdf,
    validate_upload,
)

fitz = pytest.importorskip("fitz")
docx = pytest.importorskip("docx")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


SAMPLE_LINES = [
    "Deep Learning for Software Testing",
    "",
    "Abstract",
    ("This paper reviews deep learning methods for software testing. "
     "We summarize fifty studies and compare their datasets and metrics."),
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


def test_parse_docx_table_keeps_structure_and_cells(tmp_path):
    document = docx.Document()
    document.add_paragraph("基于深度学习的人脸识别研究")
    document.add_paragraph("摘要", style="Heading 1")
    document.add_paragraph(
        "本文研究人脸识别中的特征提取方法，提出改进的卷积神经网络结构，"
        "并在公开数据集上验证了方法的有效性，实验结果表明准确率有明显提升。"
    )
    document.add_paragraph("2 实验", style="Heading 1")
    document.add_paragraph("2.1 消融实验", style="Heading 2")
    document.add_paragraph("我们比较了不同模块组合对准确率的影响。")
    document.add_paragraph("表 1 消融实验结果")
    table = document.add_table(rows=4, cols=3)
    data = [
        ["模型", "准确率（%）", "备注"],
        ["基线", "88.1", ""],
        ["本文", "88.1", "88.1"],
        ["本文-无模块", "86.0", "重复值保留"],
    ]
    for r, row in enumerate(data):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    document.add_paragraph("注：准确率为三次重复实验均值。")
    document.add_paragraph("参考文献", style="Heading 1")
    document.add_paragraph("[1] 张三. 人脸识别研究[J]. 计算机学报, 2020.")
    path = tmp_path / "table.docx"
    document.save(str(path))

    parsed = parse_docx(str(path))
    assert len(parsed["tables"]) == 1
    t = parsed["tables"][0]
    assert t["id"] == "t_0001"
    assert t["caption"] == "表 1 消融实验结果"
    assert t["section"] == "2.1 消融实验"
    assert t["source"] == "docx"
    assert t["status"] == "ok"
    assert t["header_rows"] == 1
    assert t["header_source"] == "first-row"
    assert t["units"] == "%"
    assert t["rows"][0] == ["模型", "准确率（%）", "备注"]
    assert t["rows"][1][2] == ""
    assert t["rows"][1][1] == t["rows"][2][1] == "88.1"
    assert t["footnotes"] and "准确率" in t["footnotes"][0]

    anchor = [p for p in parsed["paragraphs"] if p["id"] == t["paragraph_id"]]
    assert anchor and anchor[0]["type"] == "table"
    assert "基线 | 88.1" in anchor[0]["text"]


def test_parse_pdf_table_extraction_or_traceable_fallback(tmp_path):
    path = tmp_path / "grid.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in SAMPLE_LINES[:9] + ["2 Results", "Table 1 Ablation results"]:
        if line:
            page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    left, top, cw, rh = 72, 240, 100, 16
    rows = [
        ["Model", "Acc", "Note"],
        ["Baseline", "88.1", ""],
        ["Ours", "88.1", "88.1"],
        ["Ours-NoX", "86.0", "dup"],
    ]
    for i in range(len(rows) + 1):
        page.draw_line((left, top + i * rh), (left + cw * 3, top + i * rh),
                       color=(0, 0, 0), width=1)
    for j in range(4):
        page.draw_line((left + j * cw, top), (left + j * cw, top + len(rows) * rh),
                       color=(0, 0, 0), width=1)
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            if value:
                page.insert_text((left + j * cw + 4, top + i * rh + 11), value,
                                 fontname="helv", fontsize=9)
    page.insert_text((72, top + len(rows) * rh + 20), "Note: average of three runs.",
                     fontname="helv", fontsize=9)
    page.insert_text((72, top + len(rows) * rh + 40), "3 Conclusion",
                     fontname="helv", fontsize=10)
    page.insert_text((72, top + len(rows) * rh + 54), "The ablation supports the method.",
                     fontname="helv", fontsize=10)
    doc.save(str(path))
    doc.close()

    parsed = parse_pdf(str(path))
    assert parsed["tables"], "表格不能被静默丢弃"
    table = parsed["tables"][0]
    assert table["caption"] == "Table 1 Ablation results"
    assert table["page"] == 1
    assert table["source"] == "pdf"
    assert table["section"] == "2 Results"  # 按表格纵向位置归属，不误挂到同页后面的章节
    assert table["status"] in ("ok", "fallback")
    if table["status"] == "ok":
        assert table["rows"][0] == ["Model", "Acc", "Note"]
        assert table["rows"][1][2] == ""
        assert table["rows"][1][1] == table["rows"][2][1] == "88.1"
        assert table["footnotes"] == ["Note: average of three runs."]
    else:
        assert table["note"]
        assert "88.1" in table["fallback_text"]

    anchor = [p for p in parsed["paragraphs"] if p["id"] == table["paragraph_id"]]
    assert anchor and anchor[0]["type"] == "table"
    assert "88.1" in anchor[0]["text"]


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


def test_parse_docx_keeps_front_matter_out_of_body(tmp_path):
    document = docx.Document()
    document.add_paragraph("基于深度学习的图像识别研究")
    document.add_paragraph("作者：张三")
    document.add_paragraph("2026年5月")
    document.add_paragraph("目录")
    document.add_paragraph("摘要\tI")
    document.add_paragraph("1 绪论\t1")
    document.add_paragraph("1.1 研究背景\t2")
    document.add_paragraph("摘要", style="Heading 1")
    document.add_paragraph(
        "本文研究图像识别中的特征提取方法，提出改进的卷积神经网络结构，"
        "并在公开数据集上验证了方法的有效性，实验结果表明准确率有明显提升。"
    )
    document.add_paragraph("1 绪论", style="Heading 1")
    document.add_paragraph("2.1 需求分析", style="Heading 2")
    for item in ("支持论文上传与解析。", "支持章节结构识别。", "支持段落改写。",
                 "支持导出 Word。", "支持导出 PDF。", "支持诊断报告。"):
        document.add_paragraph(f"（{len(item)}）{item}")
    document.add_paragraph("2.2 非功能需求", style="Heading 2")
    document.add_paragraph("系统应稳定可靠。")
    path = tmp_path / "front.docx"
    document.save(str(path))

    parsed = parse_docx(str(path))
    covers = [p for p in parsed["paragraphs"] if p["type"] == "cover"]
    tocs = [p for p in parsed["paragraphs"] if p["type"] == "toc"]
    assert len(covers) == 3
    assert len(tocs) == 4
    assert all(p["section"] == "" for p in covers + tocs)
    assert all("目录" not in (s["title"] or "") for s in parsed["sections"])
    assert all(not set(s["paragraph_ids"]) & {p["id"] for p in covers + tocs}
               for s in parsed["sections"])

    merged = next(p for p in parsed["paragraphs"] if p.get("list_items"))
    assert merged["list_items"] == 6
    assert len(merged["text"].split("\n")) == 6
    assert len(merged["merged_ids"]) == 6
    assert all(pid not in merged["merged_ids"][1:]
               for s in parsed["sections"] for pid in s["paragraph_ids"])


def test_parse_pdf_front_matter_and_grouped_list(tmp_path):
    path = tmp_path / "front.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in ["A Study of Image Recognition", "Author: Zhang San", "May 2026",
                 "Contents", "Abstract ........ I", "1 Introduction ........ 1",
                 "2 Method ........ 2"]:
        page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    page = doc.new_page()
    y = 72
    for line in ["Abstract",
                 ("This paper studies image recognition methods and proposes an improved "
                  "model, with experiments showing clear improvements over baselines."),
                 "1 Introduction",
                 "Image recognition is important and widely used in many applications."]:
        page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    page = doc.new_page()
    y = 72
    for line in ["2 Method", "(1) First requirement is discussed here.",
                 "(2) Second requirement is discussed here.",
                 "(3) Third requirement is discussed here.",
                 "2.1 Details", "The method is described below."]:
        page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    doc.save(str(path))
    doc.close()

    parsed = parse_pdf(str(path))
    assert [p["type"] for p in parsed["paragraphs"][:7]] == ["cover"] * 3 + ["toc"] * 4
    assert all("Contents" not in (s["title"] or "") for s in parsed["sections"])
    merged = next(p for p in parsed["paragraphs"] if p.get("list_items"))
    assert merged["list_items"] == 3
    assert merged["text"].startswith("(1) First")
    assert "(2) Second" in merged["text"]
    assert "(3) Third" in merged["text"]


def test_parse_pdf_table_anchor_keeps_document_order(tmp_path):
    path = tmp_path / "order.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "1 Method", fontname="helv", fontsize=10)
    page.insert_text((72, 88), "Before the table.", fontname="helv", fontsize=10)
    page.insert_text((72, 104),
                     "This paragraph is long enough to satisfy the minimum text threshold.",
                     fontname="helv", fontsize=10)
    page.insert_text((72, 116),
                     "Another full sentence keeps the document above the minimum size.",
                     fontname="helv", fontsize=10)
    left, top, cw, rh = 72, 120, 100, 16
    for i in range(2):
        page.draw_line((left, top + i * rh), (left + cw * 2, top + i * rh),
                       color=(0, 0, 0), width=1)
    for j in range(3):
        page.draw_line((left + j * cw, top), (left + j * cw, top + rh),
                       color=(0, 0, 0), width=1)
    page.insert_text((left + 4, top + 11), "A", fontname="helv", fontsize=9)
    page.insert_text((left + cw + 4, top + 11), "B", fontname="helv", fontsize=9)
    page.insert_text((72, 170), "After the table.", fontname="helv", fontsize=10)
    doc.save(str(path))
    doc.close()

    parsed = parse_pdf(str(path))
    table = parsed["tables"][0]
    ids = [p["id"] for p in parsed["paragraphs"]]
    assert ids.index(table["paragraph_id"]) < ids.index(
        next(p["id"] for p in parsed["paragraphs"]
             if p["text"] == "After the table.")
    )
    assert ids.index(table["paragraph_id"]) > ids.index(
        next(p["id"] for p in parsed["paragraphs"]
             if p["text"].startswith("Before"))
    )


def test_guess_title_skips_school_and_uses_cover_title():
    paragraphs = [
        {"type": "cover", "text": "黄淮学院"},
        {"type": "cover", "text": "神经网络与深度学习"},
        {"type": "cover", "text": "题"},
        {"type": "cover", "text": "目：基于卷积神经网络的人脸表情识别"},
        {"type": "cover", "text": "班级：人工2302B"},
    ]
    assert parser._guess_title("", paragraphs, "paper.pdf") == "基于卷积神经网络的人脸表情识别"


def test_detect_numbered_heading_without_space():
    assert parser._detect_heading("3.2CNN 模型构建模块") == (2, "3.2 CNN 模型构建模块")
    assert parser._detect_heading("2.1需求分析") == (2, "2.1 需求分析")
    assert parser._detect_heading("1.5倍") is None


def test_parse_pdf_uses_indent_to_keep_whole_paragraphs(tmp_path):
    path = tmp_path / "indent.pdf"
    doc = fitz.open()
    doc.set_metadata({"title": "Testing Paper"})
    page = doc.new_page()
    page.insert_text((72, 72), "1 Introduction", fontname="helv", fontsize=10)
    page.insert_text(
        (100, 90),
        "This paper studies a new method for image recognition and it",
        fontname="helv", fontsize=10,
    )
    page.insert_text(
        (72, 106),
        "continues on this line until the sentence ends here.",
        fontname="helv", fontsize=10,
    )
    page.insert_text(
        (100, 122),
        "The second paragraph starts with an indented line and keeps",
        fontname="helv", fontsize=10,
    )
    page.insert_text(
        (72, 138),
        "its continuation in the same logical paragraph.",
        fontname="helv", fontsize=10,
    )
    doc.save(str(path))
    doc.close()

    parsed = parse_pdf(str(path))
    body = [p for p in parsed["paragraphs"] if p["type"] == "body"]
    assert len(body) == 2
    assert body[0]["text"].startswith("This paper studies")
    assert "continues on this line" in body[0]["text"]
    assert body[1]["text"].startswith("The second paragraph")
    assert "its continuation" in body[1]["text"]


def test_merge_same_row_split_labels():
    lines = [
        ("题", fitz.Rect(190.1, 564.6, 205.1, 579.6)),
        ("目：基于卷积神经网络的人脸表情识别", fitz.Rect(235.2, 564.6, 491.2, 579.6)),
        ("表2", fitz.Rect(249.6, 666.4, 267.9, 680.4)),
        ("卷积神经网络结构", fitz.Rect(278.4, 668.2, 362.3, 678.7)),
        ("卷积层负责提取边缘、纹理等特征", fitz.Rect(85.1, 679.9, 526.9, 696.0)),
    ]
    merged = parser._merge_same_row(lines, fitz.Rect)
    assert [t for t, _ in merged] == [
        "题目：基于卷积神经网络的人脸表情识别",
        "表2 卷积神经网络结构",
        "卷积层负责提取边缘、纹理等特征",
    ]


def test_parse_pdf_toc_without_page_numbers(tmp_path):
    path = tmp_path / "toc.pdf"
    doc = fitz.open()
    doc.set_metadata({"title": "Testing Paper"})
    page = doc.new_page()
    y = 72
    for line in [
        "Contents",
        "1 Introduction ................................",
        "2 Method ......................................",
        "3 Results .....................................",
    ]:
        page.insert_text((72, y), line, fontname="helv", fontsize=10)
        y += 14
    page = doc.new_page()
    page.insert_text((72, 72), "1 Introduction", fontname="helv", fontsize=10)
    page.insert_text(
        (72, 90),
        "This paper reviews deep learning methods for software testing and "
        "summarizes the datasets and metrics used by fifty studies.",
        fontname="helv", fontsize=10,
    )
    doc.save(str(path))
    doc.close()

    parsed = parse_pdf(str(path))
    tocs = [p for p in parsed["paragraphs"] if p["type"] == "toc"]
    assert len(tocs) == 4
    assert all(p["section"] == "" for p in tocs)
    assert all(p["title"] != "Contents" for p in parsed["sections"])
