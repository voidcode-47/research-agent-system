"""LLM 工厂测试。"""
import pytest
from unittest.mock import patch, MagicMock

from llm.base import BaseLLM, ChatResponse, ToolCall
from llm.factory import LLMFactory
from config.llm_config import PROVIDER_CONFIG, get_provider_config


class TestLLMBase:
    def test_tool_call_creation(self):
        tc = ToolCall(id="1", name="search", arguments={"q": "test"})
        assert tc.id == "1"
        assert tc.name == "search"

    def test_chat_response_has_tool_calls(self):
        resp = ChatResponse(content="hi")
        assert not resp.has_tool_calls

        resp2 = ChatResponse(content="hi", tool_calls=[ToolCall(id="1", name="x", arguments={})])
        assert resp2.has_tool_calls


class TestLLMFactory:
    def test_create_openai(self):
        with patch("config.settings.settings") as mock_s:
            mock_s.OPENAI_API_KEY = "sk-test"
            mock_s.OPENAI_BASE_URL = "https://api.openai.com/v1"
            llm = LLMFactory.create("openai")
            assert llm.provider == "openai"
            assert llm.model == "gpt-4o-mini"

    def test_create_zhipu(self):
        with patch("config.settings.settings") as mock_s:
            mock_s.ZHIPU_API_KEY = "test"
            mock_s.DEFAULT_LLM_PROVIDER = "zhipu"
            llm = LLMFactory.create("zhipu")
            assert llm.provider == "zhipu"

    def test_create_ollama(self):
        with patch("config.settings.settings") as mock_s:
            mock_s.OLLAMA_BASE_URL = "http://localhost:11434"
            mock_s.OLLAMA_MODEL = "qwen2.5:7b"
            llm = LLMFactory.create("ollama")
            assert llm.provider == "ollama"
            assert llm.is_local is True

    def test_create_lmstudio(self):
        with patch("config.settings.settings") as mock_s:
            mock_s.LMSTUDIO_BASE_URL = "http://localhost:1234"
            mock_s.LMSTUDIO_MODEL = ""
            # 服务未启动时自动发现返回空，不应崩溃
            llm = LLMFactory.create("lmstudio")
            assert llm.provider == "lmstudio"
            assert llm.is_local is True
            assert "/v1" in llm.base_url

    def test_lmstudio_explicit_model(self):
        with patch("config.settings.settings") as mock_s:
            mock_s.LMSTUDIO_BASE_URL = "http://localhost:1234"
            mock_s.LMSTUDIO_MODEL = "qwen2.5-7b"
            llm = LLMFactory.create("lmstudio", "qwen2.5-7b")
            assert llm.model == "qwen2.5-7b"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            LLMFactory.create("unknown_provider")

    def test_get_models(self):
        models = LLMFactory.get_models("openai")
        assert "gpt-4o-mini" in models


class TestHealthCheck:
    def _make_local_llm(self):
        from llm.openai_llm import OpenAICompatibleLLM
        with patch("llm.openai_llm.OpenAI"):
            return OpenAICompatibleLLM(
                api_key="x",
                base_url="http://localhost:1234/v1",
                model="",
                provider="lmstudio",
                is_local=True,
            )

    def test_unreachable(self):
        llm = self._make_local_llm()
        llm.ping = lambda: False
        llm.list_models = lambda: []
        h = llm.health_check()
        assert h["reachable"] is False
        assert h["model_ready"] is False

    def test_service_up_no_model(self):
        llm = self._make_local_llm()
        llm.ping = lambda: True
        llm.list_models = lambda: []
        h = llm.health_check()
        assert h["reachable"] is True
        assert h["models_loaded"] is False
        assert "没有加载" in h["detail"] or "未加载" in h["detail"]

    def test_model_ready(self):
        llm = self._make_local_llm()
        llm.model = "qwen2.5-7b"
        llm.ping = lambda: True
        llm.list_models = lambda: ["qwen2.5-7b"]
        h = llm.health_check()
        assert h["model_ready"] is True
        assert "qwen2.5-7b" in h["detail"]

    def test_tool_call_unsupported_fallback(self):
        """本地模型不支持 Function Calling（400）时降级为纯对话。"""
        from llm.openai_llm import OpenAICompatibleLLM
        from openai import BadRequestError

        with patch("llm.openai_llm.OpenAI") as MockOpenAI:
            client = MagicMock()
            MockOpenAI.return_value = client

            err = BadRequestError(
                "model does not support tools",
                response=MagicMock(status_code=400),
                body=None,
            )
            client.chat.completions.create.side_effect = [
                err,
                MagicMock(
                    choices=[MagicMock(
                        message=MagicMock(content="纯文本回答", tool_calls=None),
                    )],
                    usage=MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                ),
            ]
            llm = OpenAICompatibleLLM(
                api_key="x", base_url="http://localhost:1234/v1",
                model="m", provider="lmstudio", is_local=True,
            )
            resp = llm.chat(
                [{"role": "user", "content": "hi"}],
                tools=[{"name": "t", "parameters": {}}],
            )
            assert "纯文本回答" in resp.content
            # 第二次调用不应带 tools
            _, second_kwargs = client.chat.completions.create.call_args_list[1]
            assert "tools" not in second_kwargs


class TestProviderConfig:
    def test_get_provider_config(self):
        config = get_provider_config("openai")
        assert config["label"] == "OpenAI"
        assert config["supports_tools"] is True

    def test_get_unknown_raises(self):
        with pytest.raises(ValueError):
            get_provider_config("nonexistent")

    def test_all_providers_have_required_fields(self):
        for name, config in PROVIDER_CONFIG.items():
            assert "label" in config
            assert "default_model" in config
            assert "base_url" in config
            assert "models" in config
