# -*- coding: utf-8 -*-
"""研究页纯逻辑测试：预算包装、关键词提取、DOI 提取、检索计划降级、预检索。"""
from unittest.mock import MagicMock, patch

import pytest


class _FakeTool:
    """模拟搜索工具，记录调用参数。"""
    name = "web_search_cn"
    description = "搜索"
    parameters = {}

    def __init__(self):
        self.calls = []

    def to_openai_schema(self):
        return {"name": self.name}

    def execute(self, query, max_results=5):
        self.calls.append(max_results)
        return "\n".join(f"[{i}] 模拟结果标题 {i} 摘要内容" for i in range(1, max_results + 1))


class TestBudgetSearchTool:
    def test_clamp_oversized(self):
        from server import _BudgetSearchTool
        inner = _FakeTool()
        bt = _BudgetSearchTool(inner, budget=4)
        bt.execute("q", max_results=10)
        bt.execute("q")                      # 缺省也 clamp
        bt.execute("q", max_results=2)       # 小于预算不放大
        assert inner.calls == [4, 4, 2], f"clamp 失败: {inner.calls}"

    def test_name_proxy(self):
        from server import _BudgetSearchTool
        bt = _BudgetSearchTool(_FakeTool(), budget=4)
        assert bt.name == "web_search_cn"
        assert bt.to_openai_schema() == {"name": "web_search_cn"}


class TestExtractKeywords:
    def test_parse_plan(self):
        from server import _extract_keywords
        plan = (
            "1. 子主题一：工业质检\n"
            "2. 子主题二：预测性维护\n"
            "关键词：深度学习、缺陷检测、TensorFlow、CNN\n"
            "时间范围：2015-2026"
        )
        assert _extract_keywords(plan, limit=3) == ["深度学习", "缺陷检测", "TensorFlow"]

    def test_empty_plan(self):
        from server import _extract_keywords
        assert _extract_keywords("") == []
        assert _extract_keywords("没有关键词行的计划") == []


class TestExtractDois:
    def test_dedupe_and_clean(self):
        from server import _extract_dois
        text = (
            "A: https://doi.org/10.1371/journal.pone.0252573 "
            "B: DOI: 10.3390/s18082674?version=123。"
            "重复：10.1371/journal.pone.0252573。"
        )
        assert _extract_dois(text) == ["10.1371/journal.pone.0252573", "10.3390/s18082674"]

    def test_none(self):
        from server import _extract_dois
        assert _extract_dois("") == []
        assert _extract_dois("没有 DOI 的文本") == []


class TestPlanResearch:
    def test_degrade_on_llm_error(self):
        from server import _plan_research
        bad = MagicMock()
        bad.chat.side_effect = RuntimeError("限流")
        assert _plan_research(bad, "主题") == ""

    def test_short_plan_degrade(self):
        from server import _plan_research
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content="短")
        assert _plan_research(llm, "主题") == ""

    def test_normal_plan(self):
        from server import _plan_research
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content="1. 子主题\n2. 关键词：A、B\n3. 时间")
        plan = _plan_research(llm, "主题")
        assert "关键词" in plan


class TestPreSearch:
    def test_parallel_summary(self):
        from server import _pre_search
        tools = {
            "academic": _FakeTool(),
            "web": _FakeTool(),
        }
        result = _pre_search(["关键词A", "关键词B"], tools, max_results=4)
        assert "预检索" in result and "关键词A" not in result  # 结果注入，不含原始关键词
        assert len(result) > 100

    def test_no_keywords(self):
        from server import _pre_search
        assert _pre_search([], {}, 4) == ""

    def test_tool_failure_ignored(self):
        from server import _pre_search
        bad = _FakeTool()
        bad.execute = MagicMock(side_effect=RuntimeError("网络错误"))
        result = _pre_search(["kw"], {"academic": bad}, 4)
        assert result == ""  # 全部失败 → 空
