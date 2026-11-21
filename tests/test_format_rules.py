# -*- coding: utf-8 -*-
"""格式规则引擎：10 条规则与评分。"""
from rules.format_rules import check_format

import builtins
import pytest
from rules import format_rules

ABSTRACT_OK = "本文围绕深度学习的软件测试方法展开研究。" * 12  # 240 字
KEYWORDS_OK = ["深度学习", "软件测试", "综述"]


def _para(pid="p_0001", ptype="body", text="正文内容。", page=1, level=1):
    return {"id": pid, "type": ptype, "text": text, "page": page,
            "section": "", "level": level}


def _parsed(body=None, refs=None, captions=None, sections=None,
            abstract=ABSTRACT_OK, keywords=None):
    paragraphs = list(body or [_para()])
    paragraphs += list(captions or [])
    paragraphs += list(refs or [])
    return {
        "title": "测试论文",
        "abstract": abstract,
        "keywords": KEYWORDS_OK if keywords is None else keywords,
        "sections": sections or [],
        "paragraphs": paragraphs,
    }


def _ids(result):
    return [i["rule_id"] for i in result["issues"]]


def test_clean_paper_scores_100():
    parsed = _parsed(
        body=[_para(text="本文分析了现有测试方法的不足，并给出改进思路。")],
        captions=[_para("p_0002", "caption", "图 1 系统架构")],
        refs=[_para("p_0003", "reference", "[1] 张三. 软件测试方法[J]. 计算机学报, 2020, 12(3): 1-10.")],
        sections=[
            {"id": "s1", "level": 1, "title": "1 引言", "paragraph_ids": ["p_0001"]},
            {"id": "s2", "level": 1, "title": "2 方法", "paragraph_ids": []},
        ],
    )
    r = check_format(parsed)
    assert r["score"] == 100
    assert r["issues"] == []
    assert "未发现明显问题" in r["summary"]
    assert r["stats"]["references"] == 1
    assert r["stats"]["figures"] == 1


def test_missing_required_sections():
    r = check_format({})
    ids = _ids(r)
    assert ids.count("FMT-001") == 3
    assert r["stats"]["high"] == 3
    assert r["score"] == 100 - 3 * 8


def test_heading_level_jump():
    parsed = _parsed(sections=[
        {"id": "s1", "level": 1, "title": "1 引言", "paragraph_ids": []},
        {"id": "s2", "level": 3, "title": "1.1.1 细节", "paragraph_ids": []},
    ])
    r = check_format(parsed)
    fmt2 = [i for i in r["issues"] if i["rule_id"] == "FMT-002"]
    assert len(fmt2) == 1
    assert fmt2[0]["severity"] == "medium"


def test_section_number_gap():
    parsed = _parsed(sections=[
        {"id": "s1", "level": 1, "title": "1 引言", "paragraph_ids": []},
        {"id": "s2", "level": 1, "title": "3 方法", "paragraph_ids": []},
    ])
    r = check_format(parsed)
    fmt3 = [i for i in r["issues"] if i["rule_id"] == "FMT-003"]
    assert len(fmt3) == 1
    assert "从 1 跳到 3" in fmt3[0]["evidence"]


def test_heading_jump_issue_points_to_first_body_paragraph():
    parsed = _parsed(
        body=[
            _para("p_0001", "heading", "1 引言", level=1),
            _para("p_0002", "body", "引言正文。"),
            _para("p_0004", "heading", "1.1.1 细节", level=3),
            _para("p_0005", "body", "细节正文。"),
        ],
        sections=[
            {"id": "s1", "level": 1, "title": "1 引言",
             "paragraph_ids": ["p_0001", "p_0002"]},
            {"id": "s2", "level": 3, "title": "1.1.1 细节",
             "paragraph_ids": ["p_0004", "p_0005"]},
        ],
    )
    fmt2 = [i for i in check_format(parsed)["issues"] if i["rule_id"] == "FMT-002"]
    assert len(fmt2) == 1
    assert fmt2[0]["paragraph_id"] == "p_0005"


def test_figure_caption_missing():
    parsed = _parsed(body=[_para(text="实验结果如图 2 所示。")])
    fmt4 = [i for i in check_format(parsed)["issues"] if i["rule_id"] == "FMT-004"]
    assert len(fmt4) == 1
    assert fmt4[0]["paragraph_id"] == "p_0001"


def test_reference_missing_type_and_year():
    parsed = _parsed(refs=[_para("p_0003", "reference", "[1] 张三. 软件测试方法. 计算机学报")])
    fmt5 = [i for i in check_format(parsed)["issues"] if i["rule_id"] == "FMT-005"]
    assert len(fmt5) == 1
    assert "文献类型标识" in fmt5[0]["suggestion"]
    assert "出版年份" in fmt5[0]["suggestion"]
    assert fmt5[0]["paragraph_id"] == ""


def test_abstract_length_and_keyword_count():
    r = check_format(_parsed(abstract="太短。", keywords=["只有一个"]))
    ids = _ids(r)
    assert "FMT-006" in ids
    assert "FMT-007" in ids


def test_half_width_punctuation_and_long_paragraph():
    r = check_format(_parsed(body=[
        _para("p_0001", text="本文使用 Python,并进行了测试."),
        _para("p_0002", text="字" * 1200),
    ]))
    ids = _ids(r)
    assert "FMT-008" in ids
    assert "FMT-009" in ids


def test_citation_out_of_range_and_gap():
    parsed = _parsed(
        body=[_para(text="已有研究[3]表明该方法有效。")],
        refs=[_para("p_0002", "reference", "[1] 张三. 方法[J]. 学报, 2020.")],
    )
    fmt10 = [i for i in check_format(parsed)["issues"] if i["rule_id"] == "FMT-010"]
    assert {i["severity"] for i in fmt10} == {"high", "medium"}
    assert any("超出参考文献条数" in i["evidence"] for i in fmt10)
    assert any("缺失引用编号" in i["evidence"] for i in fmt10)


def test_score_is_clamped_at_zero():
    parsed = {
        "title": "x",
        "abstract": "",
        "keywords": [],
        "sections": [],
        "paragraphs": [_para(text="图 1 缺失。表 2 缺失。[9] 引用。" + "字" * 1100)],
    }
    r = check_format(parsed)
    assert r["score"] >= 0
    assert r["score"] == max(0, 100 - (
        8 * r["stats"]["high"] + 4 * r["stats"]["medium"] + 2 * r["stats"]["low"]
    ))


def test_template_recorded_in_stats():
    r = check_format(_parsed(), {"template": "ieee"})
    assert r["stats"]["template"] == "ieee"


@pytest.mark.parametrize("number", ["999999999", "9" * 5000], ids=["large", "overlong"])
def test_citation_check_is_bounded_for_untrusted_numbers(monkeypatch, number):
    def bounded_range(*args):
        values = builtins.range(*args)
        assert len(values) <= 1000, "citation value must not control iteration size"
        return values

    monkeypatch.setattr(format_rules, "range", bounded_range, raising=False)
    result = check_format(_parsed(
        body=[_para(text=f"已有研究[{number}]。")],
        refs=[_para("ref", "reference", "[1] 作者. 方法[J]. 学报, 2024.")],
    ))
    issues = [i for i in result["issues"] if i["rule_id"] == "FMT-010"]
    assert any(i["severity"] == "high" for i in issues)
    assert len(issues) <= 2


def test_citation_gaps_only_list_available_reference_numbers():
    result = check_format(_parsed(
        body=[_para(text="已有研究[999]。")],
        refs=[_para("ref", "reference", "[1] 作者. 方法[J]. 学报, 2024.")],
    ))
    gap = next(i for i in result["issues"]
               if i["rule_id"] == "FMT-010" and i["severity"] == "medium")
    assert gap["evidence"] == "缺失引用编号：1"
