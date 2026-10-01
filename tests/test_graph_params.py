# -*- coding: utf-8 -*-
"""编排图参数透传测试：max_iterations / detailed 传递到子 Agent。"""
from unittest.mock import MagicMock, patch

import orchestration.graph as g


class TestGraphParams:
    def test_max_iterations_passed(self):
        called = {}

        def fake_researcher(**kw):
            called.update(kw)
            return MagicMock()

        with patch.object(g, "ResearcherAgent", fake_researcher):
            g.build_research_graph(llm=MagicMock(), tools=[], max_iterations=5)
        assert called.get("max_iterations") == 5

    def test_detailed_passed_to_summarizer(self):
        called = {}

        def fake_summarizer(**kw):
            called.update(kw)
            return MagicMock()

        with patch.object(g, "SummarizerAgent", fake_summarizer):
            g.build_research_graph(llm=MagicMock(), tools=[], detailed=True)
        assert called.get("detailed") is True

    def test_defaults(self):
        called = {}

        def fake_researcher(**kw):
            called.update(kw)
            return MagicMock()

        with patch.object(g, "ResearcherAgent", fake_researcher):
            g.build_research_graph(llm=MagicMock(), tools=[])
        assert called.get("max_iterations") == 8
