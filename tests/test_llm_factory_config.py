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


@pytest.mark.parametrize("provider,key_env,base_env", [
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
    ("zhipu", "ZHIPU_API_KEY", "ZHIPU_BASE_URL"),
    ("qwen", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"),
])
def test_cloud_uses_configured_credentials_and_address(monkeypatch, provider, key_env, base_env):
    monkeypatch.setattr(settings, key_env, "configured-key")
    monkeypatch.setattr(settings, base_env, "https://gateway.example.com/v4/chat/completions")

    llm = LLMFactory.create(provider)

    assert llm.provider == provider
    assert llm.api_key == "configured-key"
    assert llm.base_url == "https://gateway.example.com/v4"
    assert llm.is_local is False
    assert LLMFactory.create(provider) is llm


@pytest.mark.parametrize("field,value", [
    ("DEEPSEEK_API_KEY", "updated-key"),
    ("DEEPSEEK_BASE_URL", "https://updated.example.com/v1"),
])
def test_saved_cloud_configuration_changes_replace_cached_client(monkeypatch, field, value):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "initial-key")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://initial.example.com/v1")
    initial = LLMFactory.create("deepseek")

    monkeypatch.setattr(settings, field, value)
    updated = LLMFactory.create("deepseek")

    assert updated is not initial
    assert updated.api_key == settings.DEEPSEEK_API_KEY
    assert updated.base_url == settings.DEEPSEEK_BASE_URL
    assert LLMFactory.create("deepseek") is updated


def test_temporary_credentials_bypass_cache_without_replacing_saved_client(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "saved-key")
    monkeypatch.setattr(settings, "DEEPSEEK_BASE_URL", "https://saved.example.com/v1")
    saved = LLMFactory.create("deepseek")

    temporary = LLMFactory.create(
        "deepseek", api_key="temporary-key", base_url="https://temporary.example.com",
    )

    assert temporary is not saved
    assert temporary.api_key == "temporary-key"
    assert temporary.base_url == "https://temporary.example.com/v1"
    assert LLMFactory.create("deepseek") is saved


def test_temporary_client_is_not_saved_in_an_empty_cache(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "saved-key")
    temporary = LLMFactory.create("deepseek", api_key="temporary-key")

    saved = LLMFactory.create("deepseek")

    assert saved is not temporary
    assert saved.api_key == "saved-key"


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
