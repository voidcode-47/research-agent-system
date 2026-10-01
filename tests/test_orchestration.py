"""多智能体编排测试。"""
from unittest.mock import MagicMock

from orchestration.supervisor import Supervisor
from llm.base import ChatResponse


class TestSupervisorRouting:
    def test_empty_query_direct(self):
        s = Supervisor()
        assert s.route_task({"query": ""}) == "answer_direct"

    def test_greeting_direct(self):
        s = Supervisor()
        assert s.route_task({"query": "你好"}) == "answer_direct"
        assert s.route_task({"query": "hello"}) == "answer_direct"

    def test_knowledge_question_goes_research(self):
        """知识性问题即使含'什么是'也必须走研究流程。"""
        s = Supervisor()
        assert s.route_task({"query": "什么是 Transformer 的注意力机制原理？"}) == "research"
        assert s.route_task({"query": "2024 年大模型推理优化进展"}) == "research"

    def test_direct_answer_node(self):
        llm = MagicMock()
        llm.chat.return_value = ChatResponse(content="你好，我是研究助手")
        s = Supervisor(llm=llm)
        node = s.direct_answer_fn()
        result = node({"query": "你好"})
        assert result["summary"] == "你好，我是研究助手"

    def test_quality_check_max_retries(self):
        s = Supervisor()
        assert s.check_quality({"iteration": 2, "summary": "x" * 100}) == "pass"


class TestGraphBuild:
    def test_graph_compiles(self):
        """工作流可以成功构建。"""
        from orchestration.graph import build_research_graph

        llm = MagicMock()
        llm.chat.return_value = ChatResponse(content="ok")
        graph = build_research_graph(llm=llm, tools=[])
        assert graph is not None
