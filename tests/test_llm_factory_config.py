"""模型配置热更新与临时连接参数的回归测试，不访问实际模型服务。"""
from unittest.mock import MagicMock

import pytest

from config.settings import settings
from llm import factory
from llm.factory import LLMFactory


@pytest.fixture(autouse=True)
def isolated_clients(monkeypatch):
    factory._llm_cache.clear()
    monkeypatch.setattr(
        "llm.openai_llm.OpenAI", MagicMock(),
    )
    yield
    factory._llm_cache.clear()


def test_default_factory_uses_saved_model_not_provider_default(monkeypatch):
    from utils import prefs
    monkeypatch.setattr(prefs, "load_prefs", lambda: {"provider": "ollama", "model": "chosen-model"})
    llm = LLMFactory.create()
    assert llm.model == "chosen-model"


@pytest.mark.parametrize("provider", ["deepseek", "zhipu", "qwen"])
def test_removed_cloud_provider_cannot_be_created(provider):
    with pytest.raises(ValueError, match="已移除"):
        LLMFactory.create(provider)


@pytest.mark.parametrize("provider,base_env", [
    ("ollama", "OLLAMA_BASE_URL"),
    ("lmstudio", "LMSTUDIO_BASE_URL"),
])
@pytest.mark.parametrize("suffix", ["", "/v1/"])
def test_local_base_url_is_normalized_once(monkeypatch, provider, base_env, suffix):
    monkeypatch.setattr(settings, base_env, "http://127.0.0.1:1234" + suffix)

    llm = LLMFactory.create(provider, "local-model")

    assert llm.base_url == "http://127.0.0.1:1234/v1"
    assert llm.is_local is True


@pytest.mark.parametrize("provider,base_env", [
    ("ollama", "OLLAMA_BASE_URL"),
    ("lmstudio", "LMSTUDIO_BASE_URL"),
])
def test_local_address_changes_and_temporary_overrides(monkeypatch, provider, base_env):
    monkeypatch.setattr(settings, base_env, "http://127.0.0.1:1234")
    initial = LLMFactory.create(provider, "local-model")
    monkeypatch.setattr(settings, base_env, "http://127.0.0.1:11434/v1")

    updated = LLMFactory.create(provider, "local-model")
    temporary = LLMFactory.create(
        provider, "local-model", base_url="http://127.0.0.1:4321/v1",
    )

    assert updated is not initial
    assert updated.base_url == "http://127.0.0.1:11434/v1"
    assert temporary.base_url == "http://127.0.0.1:4321/v1"
    assert LLMFactory.create(provider, "local-model") is updated


def test_custom_configuration_is_not_cached(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_API_BASE_URL", "https://custom.example.com")
    monkeypatch.setattr(settings, "CUSTOM_API_KEY", "custom-key")
    monkeypatch.setattr(settings, "CUSTOM_API_MODEL", "custom-model")

    first = LLMFactory.create("custom")
    second = LLMFactory.create("custom")

    assert first is not second
    assert first.base_url == "https://custom.example.com/v1"
    assert first.model == "custom-model"


@pytest.mark.parametrize("protocol,base_url,llm_type", [
    ("openai", "https://gateway.example.test/v1", "OpenAICompatibleLLM"),
    ("anthropic", "https://api.anthropic.com", "CustomProtocolLLM"),
    ("gemini", "https://generativelanguage.googleapis.com", "CustomProtocolLLM"),
])
def test_custom_service_factory_selects_protocol_and_model(tmp_path, monkeypatch, protocol, base_url, llm_type):
    from utils import custom_providers

    monkeypatch.setattr(custom_providers, "CUSTOM_PROVIDERS_PATH", str(tmp_path / "services.json"))
    monkeypatch.setattr(custom_providers, "ENV_PATH", str(tmp_path / ".env"))
    service = custom_providers.create_service({
        "name": protocol, "protocol": protocol, "base_url": base_url,
        "api_key": "secret", "models": ["model-a", "model-b"],
        "default_model": "model-b",
    })

    llm = LLMFactory.create(f"custom:{service['id']}")

    assert llm.model == "model-b"
    assert type(llm).__name__ == llm_type
    if protocol == "openai":
        assert llm.base_url == base_url
    else:
        assert llm.protocol == protocol
