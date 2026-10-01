# -*- coding: utf-8 -*-
"""报告引用规范化测试：正文 [n] 与参考文献编号一致性。"""
from utils.report_refs import (
    extract_citations,
    normalize_report_references,
)


class TestExtractCitations:
    def test_first_occurrence_order(self):
        assert extract_citations("A[3]B[1]C[3]D[2]") == [3, 1, 2]

    def test_empty(self):
        assert extract_citations("无引用") == []


class TestNormalizeReferences:
    def test_renumber_by_first_occurrence(self):
        md = (
            "## 摘要\n结论[3]和[1]支撑。\n\n"
            "## 详细分析\n要点[2]。\n\n"
            "## 参考文献\n"
            "1. 文献A\n"
            "2. 文献B\n"
            "3. 文献C\n"
        )
        out = normalize_report_references(md)
        # 正文按首次出现顺序重编号：3→1, 1→2, 2→3
        assert "结论[1]和[2]支撑" in out
        assert "要点[3]" in out
        assert "1. 文献C" in out
        assert "2. 文献A" in out
        assert "3. 文献B" in out

    def test_dash_items_no_number(self):
        md = (
            "## 正文\n引用[2]和[1]。\n\n"
            "## 参考文献\n"
            "- 文献X\n"
            "- 文献Y\n"
        )
        out = normalize_report_references(md)
        assert "引用[1]和[2]" in out
        assert "1. 文献Y" in out   # 正文先引 [2] → 原第 2 条放第 1
        assert "2. 文献X" in out

    def test_bracket_numbered_items(self):
        md = (
            "## 正文\n引用[1]。\n\n"
            "## 参考文献\n"
            "[1] 文献A\n"
            "[2] 文献B\n"
        )
        out = normalize_report_references(md)
        assert "引用[1]" in out
        assert "1. 文献A" in out
        assert "2. 文献B" in out

    def test_untracked_items_appended(self):
        md = (
            "## 正文\n引用[1]。\n\n"
            "## 参考文献\n"
            "1. 文献A\n"
            "2. 文献B\n"
        )
        out = normalize_report_references(md)
        assert "1. 文献A" in out
        assert "2. 文献B" in out  # 正文未引用的 [2] 追加在尾部
        assert "引用[1]" in out

    def test_fabricated_citation_skipped(self):
        # 正文引 [3] 但条目只有 2 条 → 条目不足，保守原样返回，不冒险改动
        md = (
            "## 正文\n引用[3]。\n\n"
            "## 参考文献\n"
            "1. 文献A\n"
            "2. 文献B\n"
        )
        out = normalize_report_references(md)
        assert out == md

    def test_fabricated_within_budget_skipped(self):
        # 3 条条目但正文引 [3] 且条目 3 缺失 → 跳过该引用，其余正常
        md = (
            "## 正文\n引用[2]和[3]。\n\n"
            "## 参考文献\n"
            "1. 文献A\n"
            "2. 文献B\n"
            "3. 文献C\n"
        )
        out = normalize_report_references(md)
        assert "引用[1]和[2]" in out
        assert "1. 文献B" in out   # 正文先引 [2] → B 第 1
        assert "2. 文献C" in out   # 再引 [3] → C 第 2
        assert "3. 文献A" in out   # 未引用条目 A 追加尾部

    def test_insufficient_items_unchanged(self):
        md = (
            "## 正文\n引用[1][2][3]。\n\n"
            "## 参考文献\n"
            "1. 只有一条\n"
        )
        out = normalize_report_references(md)
        assert out == md  # 条目不足 → 保守原样

    def test_no_ref_heading_unchanged(self):
        md = "## 正文\n引用[1]。\n没有参考文献段落"
        assert normalize_report_references(md) == md

    def test_no_citation_unchanged(self):
        md = "## 正文\n无引用\n\n## 参考文献\n1. 文献A\n"
        assert normalize_report_references(md) == md

    def test_empty_input(self):
        assert normalize_report_references("") == ""

    def test_consecutive_citations_remap(self):
        md = (
            "## 正文\n[3][1] 是连写引用。\n\n"
            "## 参考文献\n"
            "1. A\n"
            "2. B\n"
            "3. C\n"
        )
        out = normalize_report_references(md)
        assert "[1][2] 是连写引用" in out
        assert "1. C" in out
