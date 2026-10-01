"""ReAct Agent 测试。"""
import pytest
from unittest.mock import MagicMock

from agents.react_agent import ReActAgent, AgentStep, StepType
from llm.base import ChatResponse, ToolCall
from memory.short_term import ShortTermMemory
from memory.manager import MemoryManager
from utils.safety import SafetyGuard


class MockTool:
    """模拟工具。"""
    name = "mock_tool"
    description = "测试工具"
    parameters = {"type": "object", "properties": {"input": {"type": "string"}}}

    def execute(self, **kwargs):
        return f"工具结果: {kwargs.get('input', '')}"

    def to_openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class TestReActAgent:
    def _make_agent(self, mock_llm):
        memory = MemoryManager(ShortTermMemory(max_tokens=4000))
        return ReActAgent(
            llm=mock_llm,
            tools=[MockTool()],
            memory=memory,
            safety=SafetyGuard(max_iterations=5),
        )

    def test_final_answer(self, mock_llm):
        """没有工具调用时直接返回。"""
        mock_llm.chat.return_value = ChatResponse(content="最终答案", tool_calls=[])
        agent = self._make_agent(mock_llm)

        final, steps = agent.run_sync("你好")
        assert final == "最终答案"
        assert any(s.type == StepType.FINAL for s in steps)

    def test_tool_call_flow(self, mock_llm):
        """工具调用流程。"""
        # 第一次：调用工具
        # 第二次：返回最终答案
        mock_llm.chat.side_effect = [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="1", name="mock_tool", arguments={"input": "test"})],
            ),
            ChatResponse(content="基于工具结果回答", tool_calls=[]),
        ]
        agent = self._make_agent(mock_llm)

        final, steps = agent.run_sync("调用工具")
        assert final == "基于工具结果回答"
        assert any(s.type == StepType.ACTION for s in steps)
        assert any(s.type == StepType.OBSERVATION for s in steps)

    def test_max_iterations(self, mock_llm):
        """达到最大迭代终止（每次参数不同避免循环检测）。"""
        responses = [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id=str(i), name="mock_tool", arguments={"input": f"query_{i}"})],
            )
            for i in range(10)
        ]
        mock_llm.chat.side_effect = responses
        agent = self._make_agent(mock_llm)

        final, steps = agent.run_sync("无限循环")
        assert any(s.type == StepType.ERROR for s in steps)
        assert "最大迭代" in final

    def test_loop_detection(self, mock_llm):
        """循环检测触发。"""
        mock_llm.chat.return_value = ChatResponse(
            content="",
            tool_calls=[ToolCall(id="1", name="mock_tool", arguments={"input": "same"})],
        )
        agent = self._make_agent(mock_llm)

        final, steps = agent.run_sync("循环测试")
        assert any(s.type == StepType.ERROR for s in steps)

    def test_tool_messages_well_formed(self, mock_llm):
        """工具调用必须写入标准配对格式（tool_calls + tool_call_id）。"""
        mock_llm.chat.side_effect = [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="call_abc", name="mock_tool", arguments={"input": "x"})],
            ),
            ChatResponse(content="完成", tool_calls=[]),
        ]
        agent = self._make_agent(mock_llm)
        agent.run_sync("测试")

        msgs = agent.memory.short.messages
        assistant_tc = [m for m in msgs if m.get("tool_calls")]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]

        assert len(assistant_tc) == 1
        assert assistant_tc[0]["tool_calls"][0]["id"] == "call_abc"
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_abc"
        assert tool_msgs[0]["name"] == "mock_tool"
