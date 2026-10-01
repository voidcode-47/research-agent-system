"""工具系统测试。"""
import pytest
from unittest.mock import MagicMock, patch

from tools.base import BaseTool, TOOL_REGISTRY, register_tool, list_tools, get_tools_schema


class TestToolRegistry:
    def test_register_and_get(self):
        # DuckDuckGoSearchTool 已在导入时注册
        assert "web_search" in list_tools()

    def test_get_tool(self):
        tool = TOOL_REGISTRY.get("web_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_get_schema(self):
        schemas = get_tools_schema(["web_search"])
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "web_search"

    def test_tool_call_catches_exception(self):
        class FailTool(BaseTool):
            name = "fail_tool"
            description = "always fails"
            parameters = {"type": "object", "properties": {}}
            def execute(self, **kwargs):
                raise RuntimeError("boom")

        tool = FailTool()
        result = tool(test="x")
        assert "执行失败" in result


class TestTextSplitter:
    def test_short_text(self):
        from rag.text_splitter import TextSplitter
        splitter = TextSplitter(chunk_size=100, chunk_overlap=10)
        result = splitter.split("短文本")
        assert len(result) == 1

    def test_long_text_split(self):
        from rag.text_splitter import TextSplitter
        splitter = TextSplitter(chunk_size=50, chunk_overlap=10)
        long_text = "这是第一段。\n\n这是第二段。\n\n这是第三段。" * 10
        result = splitter.split(long_text)
        assert len(result) > 1

    def test_empty_text(self):
        from rag.text_splitter import TextSplitter
        splitter = TextSplitter()
        result = splitter.split("")
        assert result == []

    def test_split_documents(self):
        from rag.text_splitter import TextSplitter
        splitter = TextSplitter(chunk_size=100)
        docs = [{"content": "测试内容" * 20, "metadata": {"source": "test"}}]
        result = splitter.split_documents(docs)
        assert len(result) >= 1
        assert "chunk_index" in result[0]["metadata"]


class TestSafetyGuard:
    def test_reset(self):
        from utils.safety import SafetyGuard
        guard = SafetyGuard()
        guard.tick_iteration()
        guard.reset()
        assert guard.iteration == 0

    def test_loop_detection(self):
        from utils.safety import SafetyGuard
        guard = SafetyGuard(max_iterations=10)
        # 同一个 action 重复
        guard.is_looping("search", {"q": "test"})
        guard.is_looping("other", {"q": "x"})
        result = guard.is_looping("search", {"q": "test"})
        assert result is True

    def test_over_iterations(self):
        from utils.safety import SafetyGuard
        guard = SafetyGuard(max_iterations=5)
        for _ in range(5):
            guard.tick_iteration()
        assert guard.over_iterations is True

    def test_critical_threshold(self):
        from utils.safety import SafetyGuard
        guard = SafetyGuard(max_iterations=10)
        for _ in range(8):
            guard.tick_iteration()
        assert guard.is_critical is True
