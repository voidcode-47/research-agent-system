"""记忆系统测试，重点验证 Function Calling 消息配对完整性。"""
from memory.short_term import ShortTermMemory


class TestShortTermMemory:
    def test_add_simple_message(self):
        m = ShortTermMemory()
        m.add("user", "你好")
        msgs = m.get_messages()
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "你好"

    def test_tool_call_pair_preserved(self):
        """assistant(tool_calls) 与 tool 消息必须成组保留。"""
        m = ShortTermMemory(max_tokens=10000)
        m.add("user", "查一下天气")
        m.add_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "天气"}'},
            }],
        })
        m.add_message({
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "web_search",
            "content": "晴天 25 度",
        })
        m.add("assistant", "今天晴天 25 度")

        msgs = m.get_messages()
        roles = [x["role"] for x in msgs]
        assert "tool" in roles
        # tool 前必须有带 tool_calls 的 assistant
        tool_idx = roles.index("tool")
        assert msgs[tool_idx - 1].get("tool_calls")
        assert msgs[tool_idx]["tool_call_id"] == "call_1"

    def test_no_orphan_tool_messages(self):
        """窗口截断时不得遗留孤立的 tool 消息。"""
        m = ShortTermMemory(max_tokens=200)
        m.add("user", "很长的问题" * 50)
        m.add_message({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "t", "arguments": "{}"}}],
        })
        m.add_message({
            "role": "tool",
            "tool_call_id": "c1",
            "name": "t",
            "content": "结果" * 50,
        })
        m.add("user", "最新问题")

        msgs = m.get_messages()
        roles = [x["role"] for x in msgs]
        # 任何 tool 消息都必须有前导 tool_calls assistant
        for i, r in enumerate(roles):
            if r == "tool":
                assert msgs[i - 1].get("tool_calls") is not None
        # 孤立的 tool_calls assistant（无 tool 跟随）也不应出现
        for i, msg in enumerate(msgs):
            if msg.get("tool_calls"):
                assert i + 1 < len(msgs) and msgs[i + 1]["role"] == "tool"

    def test_system_prompt_replace(self):
        m = ShortTermMemory()
        m.add_system("提示A")
        m.add_system("提示B")
        sys_msgs = [x for x in m.messages if x["role"] == "system"]
        assert len(sys_msgs) == 1
        assert sys_msgs[0]["content"] == "提示B"

    def test_clear(self):
        m = ShortTermMemory()
        m.add("user", "hi")
        m.clear()
        assert m.messages == []
        assert m.summary == ""
