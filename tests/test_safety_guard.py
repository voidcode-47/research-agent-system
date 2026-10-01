"""安全守卫测试：破坏性命令检测 + ReAct 运行时拦截。"""
from unittest.mock import MagicMock

from agents.react_agent import ReActAgent, StepType
from llm.base import ChatResponse, ToolCall
from memory.manager import MemoryManager
from memory.short_term import ShortTermMemory
from utils.safety import SafetyGuard, DESTRUCTIVE_PATTERNS, _collect_strings


class DangerousShellTool:
    """模拟一个危险的 shell 工具（dangerous=True）。"""

    name = "shell"
    description = "执行 shell 命令（危险工具）"
    dangerous = True
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}

    def __init__(self):
        self.executed = False

    def execute(self, **kwargs):
        self.executed = True
        return f"执行了: {kwargs.get('command')}"

    def to_openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class TestDestructiveDetection:
    """check_destructive 纯模式匹配。"""

    BLOCKED = [
        "rm -rf /",
        "rmdir /tmp/x",
        "git push -f origin main",
        "git push --force",
        "mkfs.ext4 /dev/sda1",
        "format C:",
        "对D盘进行格式化",
        "dd if=/dev/zero of=/dev/sda",
        "shutil.rmtree('./data')",
        "os.remove('a.txt')",
        "os.unlink('b.txt')",
        "DEL /Q *",
        "rd /s /q C:\\temp",
    ]

    ALLOWED = [
        "ls -la",
        "echo hello world",
        "git push origin main",
        "cat README.md",
        "python app.py",
        "find . -name x",
    ]

    def test_blocks_destructive_commands(self):
        guard = SafetyGuard()
        for cmd in self.BLOCKED:
            reason = guard.check_destructive("shell", {"command": cmd})
            assert reason is not None, f"应拦截: {cmd}"

    def test_allows_benign_commands(self):
        guard = SafetyGuard()
        for cmd in self.ALLOWED:
            reason = guard.check_destructive("shell", {"command": cmd})
            assert reason is None, f"不应拦截: {cmd}"

    def test_returns_human_readable_reason(self):
        guard = SafetyGuard()
        reason = guard.check_destructive("shell", {"command": "rm -rf /"})
        assert reason and "破坏性操作" in reason and "rm" in reason

    def test_collect_strings_handles_nested(self):
        # 嵌套结构里的字符串也要被检测到
        guard = SafetyGuard()
        reason = guard.check_destructive("shell", {"opts": {"inner": {"cmd": "rm -rf /"}}})
        assert reason is not None

    def test_empty_arguments(self):
        guard = SafetyGuard()
        assert guard.check_destructive("shell", {}) is None
        assert guard.check_destructive("shell", None) is None

    def test_patterns_nonempty(self):
        assert len(DESTRUCTIVE_PATTERNS) > 0


class TestReActRuntimeBlock:
    """危险工具调用破坏性命令时，ReAct 运行时拦截、不真正执行。"""

    def _make_agent(self, tool, mock_llm):
        memory = MemoryManager(ShortTermMemory(max_tokens=4000))
        return ReActAgent(
            llm=mock_llm,
            tools=[tool],
            memory=memory,
            safety=SafetyGuard(max_iterations=5),
        )

    def test_destructive_call_is_blocked(self, mock_llm):
        tool = DangerousShellTool()
        mock_llm.chat.side_effect = [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="1", name="shell", arguments={"command": "rm -rf /tmp/data"})],
            ),
            ChatResponse(content="已按要求停止，未执行删除。", tool_calls=[]),
        ]
        agent = self._make_agent(tool, mock_llm)

        final, steps = agent.run_sync("删除 /tmp/data")

        observations = [s.content for s in steps if s.type == StepType.OBSERVATION]
        assert any("安全拦截" in o for o in observations), "应有拦截观察"
        assert any("rm" in o for o in observations)
        assert tool.executed is False, "危险命令不得真正执行"

    def test_benign_dangerous_tool_call_runs(self, mock_llm):
        """危险工具但命令本身无害（如 ls）时，应正常执行。"""
        tool = DangerousShellTool()
        mock_llm.chat.side_effect = [
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="1", name="shell", arguments={"command": "ls -la"})],
            ),
            ChatResponse(content="done", tool_calls=[]),
        ]
        agent = self._make_agent(tool, mock_llm)

        final, steps = agent.run_sync("列目录")
        assert tool.executed is True, "无害命令应正常执行"
        assert any("执行了" in s.content for s in steps if s.type == StepType.OBSERVATION)
