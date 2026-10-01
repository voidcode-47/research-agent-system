# -*- coding: utf-8 -*-
"""OpenAI 兼容 LLM 测试：max_tokens 透传、429 限流友好提示、Function Calling 降级。"""
from unittest.mock import MagicMock, patch

import pytest

from llm.openai_llm import OpenAICompatibleLLM


class _FakeResp:
    def __init__(self, content="ok", tool_calls=None):
        self.choices = [MagicMock(
            message=MagicMock(content=content, tool_calls=tool_calls),
            finish_reason="stop",
        )]
        self.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)


class _FakeCompletions:
    def __init__(self, side_effect=None):
        self._side = side_effect

    def create(self, **kwargs):
        if self._side:
            return self._side(**kwargs)
        return _FakeResp()


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


def _make_llm(completions):
    client = MagicMock()
    client.chat = _FakeChat(completions)
    with patch("llm.openai_llm.OpenAI", return_value=client):
        return OpenAICompatibleLLM(
            provider="openai",
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="gpt-test",
            is_local=False,
        )


class TestOpenAICompatibleLLM:
    def test_chat_passes_max_tokens(self):
        seen = {}

        def side(**kwargs):
            seen.update(kwargs)
            return _FakeResp()

        llm = _make_llm(_FakeCompletions(side))
        resp = llm.chat([{"role": "user", "content": "hi"}], max_tokens=4096)
        assert seen.get("max_tokens") == 4096
        assert resp.content == "ok"
        assert seen["model"] == "gpt-test"

    def test_chat_without_max_tokens_omits(self):
        seen = {}

        def side(**kwargs):
            seen.update(kwargs)
            return _FakeResp()

        llm = _make_llm(_FakeCompletions(side))
        llm.chat([{"role": "user", "content": "hi"}])
        assert "max_tokens" not in seen

    def test_rate_limit_minute_level_friendly(self):
        from openai import RateLimitError
        exc = RateLimitError(
            "rate limit: rpm exhausted",
            response=MagicMock(status_code=429),
            body={},
        )

        def side(**kwargs):
            raise exc

        llm = _make_llm(_FakeCompletions(side))
        resp = llm.chat([{"role": "user", "content": "hi"}])
        assert "API 限流" in resp.content
        assert "1~2 分钟" in resp.content

    def test_function_calling_fallback(self):
        """模型不支持 tools 时自动降级为纯对话重试。"""
        from openai import BadRequestError
        exc = BadRequestError(
            "400 invalid tool calls",
            response=MagicMock(status_code=400),
            body={},
        )
        calls = {"n": 0}

        class _F:
            def create(self, **kwargs):
                calls["n"] += 1
                if "tools" in kwargs and calls["n"] == 1:
                    raise exc
                return _FakeResp(content="纯对话回答")
        llm = _make_llm(_FakeCompletions(_F().create))
        resp = llm.chat(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "x"}}],
        )
        assert calls["n"] == 2
        assert "纯对话回答" in resp.content
        assert "不支持工具调用" in resp.content

    def test_unknown_tool_name_no_side_effect(self):
        pass  # 占位：工具名不匹配逻辑在 ReActAgent 层覆盖
