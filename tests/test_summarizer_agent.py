# -*- coding: utf-8 -*-
"""总结员 Agent 测试：详细版提示词、max_tokens、引用池、失败降级。"""
from unittest.mock import MagicMock

from agents.summarizer_agent import SummarizerAgent
from config.prompts import (
    SUMMARIZER_SYSTEM_PROMPT,
    SUMMARIZER_SYSTEM_PROMPT_DETAILED,
)


def _state():
    return {
        "query": "测试主题",
        "research_data": ["资料A：方法X", "研究结论: 结论Y"],
        "analysis": "分析内容",
        "conflicts": [],
        "sources": [
            {"tool": "academic_search", "args": {"query": "大语言模型"}},
            {"tool": "web_scraper", "args": {"url": "https://doi.org/10.1000/abc"}},
        ],
    }


class TestSummarizerAgent:
    def test_detailed_prompt_and_max_tokens(self):
        captured = {}
        llm = MagicMock()
        llm.chat.side_effect = lambda messages, **kw: captured.update(
            sys=messages[0]["content"], kw=kw
        ) or MagicMock(content="报告")
        agent = SummarizerAgent(llm, detailed=True)
        result = agent.node_fn()(_state())
        assert result["status"] == "summary_done"
        assert captured["sys"] == SUMMARIZER_SYSTEM_PROMPT_DETAILED
        assert captured["kw"].get("max_tokens") == 8192
        assert "800~1500" in captured["sys"]
        assert "## 引言与研究背景" in captured["sys"]

    def test_standard_prompt(self):
        captured = {}
        llm = MagicMock()
        llm.chat.side_effect = lambda messages, **kw: captured.update(
            sys=messages[0]["content"]
        ) or MagicMock(content="报告")
        SummarizerAgent(llm, detailed=False).node_fn()(_state())
        assert captured["sys"] == SUMMARIZER_SYSTEM_PROMPT

    def test_sources_reference_pool_injected(self):
        captured = {}
        llm = MagicMock()
        llm.chat.side_effect = lambda messages, **kw: captured.update(
            user=messages[1]["content"]
        ) or MagicMock(content="报告")
        SummarizerAgent(llm, detailed=True).node_fn()(_state())
        assert "academic_search" in captured["user"]
        assert "10.1000/abc" in captured["user"]
        assert "参考文献" in captured["user"]

    def test_llm_failure_fallback(self):
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("服务不可用")
        agent = SummarizerAgent(llm, detailed=True)
        result = agent.node_fn()(_state())
        assert "报告生成失败" in result["summary"]
        assert "分析内容" in result["summary"]
