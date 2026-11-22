"""论文问答分块与检索：章节层级、长段切分、表格块、预算与引用过滤。"""
from paper import chunks


def _p(pid, text, ptype="body", page=1, level=0, section=""):
    return {"id": pid, "type": ptype, "text": text, "page": page,
            "level": level, "section": section}


def _parsed(paragraphs, sections=None, tables=None, title="测试论文", abstract=""):
    return {
        "title": title,
        "abstract": abstract,
        "keywords": [],
        "sections": sections or [],
        "paragraphs": paragraphs,
        "tables": tables or [],
    }


def test_section_paths_keep_full_hierarchy():
    sections = [
        {"id": "s1", "title": "第二章 方法", "level": 1},
        {"id": "s2", "title": "2.1 模型", "level": 2},
        {"id": "s3", "title": "2.1.1 数据", "level": 3},
        {"id": "s4", "title": "2.2 实验", "level": 2},
        {"id": "s5", "title": "第三章 结论", "level": 1},
    ]
    paths = chunks.section_paths(sections)
    assert paths["s1"] == "第二章 方法"
    assert paths["s3"] == "第二章 方法 / 2.1 模型 / 2.1.1 数据"
    assert paths["s4"] == "第二章 方法 / 2.2 实验"
    assert paths["s5"] == "第三章 结论"


def test_build_keeps_unsectioned_body_and_skips_title_and_abstract_duplicate():
    sections = [
        {"id": "s1", "title": "摘要", "level": 1, "paragraph_ids": ["p_0002"]},
        {"id": "s2", "title": "第一章 绪论", "level": 1, "paragraph_ids": ["p_0003"]},
        {"id": "s3", "title": "1.1 背景", "level": 2, "paragraph_ids": ["p_0004", "p_0005"]},
    ]
    paragraphs = [
        _p("p_0001", "测试论文"),
        _p("p_0002", "本文研究深度学习，摘要正文足够长。"),
        _p("p_0003", "第一章开头的总述段落。"),
        _p("p_0004", "背景段落一。"),
        _p("p_0005", "背景段落二。"),
    ]
    index = chunks.build(_parsed(paragraphs, sections, abstract="本文研究深度学习，摘要正文足够长。"))
    ids = [c["id"] for c in index["paragraphs"]]
    assert "p_0001" not in ids   # 标题已在概览中
    assert "p_0002" not in ids   # 摘要已在概览中
    assert "p_0003" in ids
    by_id = {c["id"]: c for c in index["paragraphs"]}
    assert by_id["p_0003"]["section_path"] == "第一章 绪论"
    assert by_id["p_0004"]["section_path"] == "第一章 绪论 / 1.1 背景"

    unsectioned = chunks.build(_parsed([_p("p_0100", "没有章节归属的正文。")]))
    assert unsectioned["paragraphs"][0]["section_path"] == ""


def test_long_paragraph_splits_at_sentences_with_offsets():
    sentence = "我们比较了不同模型在公开数据集上的准确率。"
    text = sentence * 40
    assert len(text) > chunks.MAX_PARA_CHARS
    index = chunks.build(_parsed([_p("p_0001", text)]))
    pieces = index["paragraphs"]
    assert len(pieces) >= 2
    assert [c["key"] for c in pieces] == [f"p_0001#{i}" for i in range(len(pieces))]
    assert pieces[0]["start"] == 0
    assert pieces[-1]["end"] == len(text)
    assert "".join(p["text"] for p in pieces) == text
    for i, piece in enumerate(pieces):
        assert piece["segments"] == len(pieces)
        assert piece["segment"] == i
        assert len(piece["text"]) <= chunks.MAX_PARA_CHARS
        if i:
            assert piece["start"] == pieces[i - 1]["end"]

    _context, evidence = chunks.search(index, "公开数据集准确率")
    assert evidence and evidence[0]["id"] == "p_0001"
    assert evidence[0]["segment"] == 0


def _long_table(rows=24):
    header = ["模型", "准确率（%）", "备注"]
    body = [[f"模型{i}", f"{80 + i % 10}.1", "重复值"] for i in range(rows)]
    body[1][2] = ""  # 保留空单元格
    body[2][1] = body[1][1]  # 保留重复值
    return {
        "id": "t_0001", "caption": "表 1 消融实验结果", "section_id": "s2",
        "section": "2.1 消融实验", "page": 3,
        "rows": [header] + body, "header_rows": 1,
        "units": "%", "footnotes": ["注：均值为三次实验。"],
        "source": "docx", "status": "ok",
    }


def test_table_chunks_repeat_caption_header_units_and_keep_cells():
    sections = [
        {"id": "s1", "title": "第二章 实验", "level": 1, "paragraph_ids": ["p_0001"]},
        {"id": "s2", "title": "2.1 消融实验", "level": 2, "paragraph_ids": []},
    ]
    index = chunks.build(_parsed([_p("p_0001", "实验设置。")], sections, [_long_table(rows=60)]))
    table_chunks = index["tables"]
    assert len(table_chunks) >= 2
    for chunk in table_chunks:
        assert chunk["section_path"] == "第二章 实验 / 2.1 消融实验"
        assert "表题：表 1 消融实验结果" in chunk["text"]
        assert "表头：模型 | 准确率（%） | 备注" in chunk["text"]
        assert "单位：%" in chunk["text"]
        assert chunk["caption"] == "表 1 消融实验结果"

    row_lines = [line for chunk in table_chunks
                 for line in chunk["text"].splitlines() if line.startswith("第 ")]
    assert len(row_lines) == 60
    assert any(line.endswith("模型1 | 81.1 | ") for line in row_lines)  # 空单元格
    assert any("| 81.1 | 重复值" in line for line in row_lines)  # 重复值保留

    context, evidence = chunks.search(index, "消融实验 准确率")
    assert "[t_0001" in context
    assert "第三章" not in context
    assert any(e["kind"] == "table" and e["id"] == "t_0001" for e in evidence)


def test_table_without_rows_keeps_fallback_text():
    table = {
        "id": "t_0002", "caption": "", "section": "实验结果", "page": 2,
        "rows": [], "header_rows": 0, "units": "", "footnotes": [],
        "source": "pdf", "status": "fallback",
        "note": "未能识别表格结构，已保留原文",
        "fallback_text": "模型 准确率 88.1",
    }
    index = chunks.build(_parsed([], [], [table]))
    assert len(index["tables"]) == 1
    assert "表格结构未提取" in index["tables"][0]["text"]
    context, evidence = chunks.search(index, "准确率 88.1")
    assert "模型 准确率 88.1" in context
    assert evidence and evidence[0]["kind"] == "table"


def test_budget_long_candidate_does_not_block_short_match():
    sections = [{"id": "s1", "title": "实验", "level": 1, "paragraph_ids": ["p_0001"]}]
    index = chunks.build(_parsed(
        [_p("p_0001", "消融实验准确率对比。")], sections, [_long_table(rows=40)],
    ))
    context, evidence = chunks.search(index, "消融实验准确率", max_chars=600)
    assert len(context) <= 600
    assert "消融实验准确率对比。" in context
    assert any(e["kind"] == "paragraph" and e["id"] == "p_0001" for e in evidence)


def test_empty_question_and_no_hit():
    index = chunks.build(_parsed([_p("p_0001", "深度学习在文本分类中被广泛使用。")]))
    context, evidence = chunks.search(index, "")
    assert evidence == []
    assert "深度学习在文本分类" not in context
    assert "未检索到" not in context

    context, evidence = chunks.search(index, "量子纠缠")
    assert evidence == []
    assert "未检索到与问题直接相关的段落或表格" in context


def test_references_only_recalled_when_asked():
    sections = [
        {"id": "s1", "title": "1 引言", "level": 1, "paragraph_ids": ["p_0001"]},
        {"id": "s2", "title": "参考文献", "level": 1, "paragraph_ids": ["p_0002"]},
    ]
    paragraphs = [
        _p("p_0001", "深度学习用于文本分类，准确率较高。"),
        _p("p_0002", "[1] 张三. 深度学习研究[J]. 2020.", ptype="reference"),
    ]
    index = chunks.build(_parsed(paragraphs, sections))
    _, evidence = chunks.search(index, "深度学习")
    assert all(e["id"] != "p_0002" for e in evidence)

    _, evidence = chunks.search(index, "参考文献中的深度学习")
    assert any(e["id"] == "p_0002" for e in evidence)


def test_context_marks_source_page_and_section_and_adds_neighbors():
    sections = [{
        "id": "s1", "title": "第二章 方法", "level": 1,
        "paragraph_ids": ["p_0001", "p_0002", "p_0003"],
    }]
    paragraphs = [
        _p("p_0001", "前一段介绍实验设置。", page=2),
        _p("p_0002", "本章提出一种新的图神经网络方法。", page=3),
        _p("p_0003", "后一段总结实验结果。", page=3),
    ]
    context, evidence = chunks.search(chunks.build(_parsed(paragraphs, sections)),
                                      "图神经网络方法")
    assert "[p_0002" in context
    assert "第3页" in context
    assert "第二章 方法" in context
    assert "相邻段落" in context
    assert evidence[0]["id"] == "p_0002"
    assert len({(e["kind"], e["id"]) for e in evidence}) == len(evidence)


def test_old_parsed_without_sections_or_tables_still_searchable():
    parsed = {"title": "旧论文", "paragraphs": [
        {"id": "p_0001", "type": "body", "text": "旧数据中没有章节与表格字段。", "page": 1},
    ]}
    context, evidence = chunks.search(chunks.build(parsed), "旧数据 章节 表格")
    assert evidence and evidence[0]["id"] == "p_0001"
    assert "旧数据中没有章节与表格字段。" in context
