"""多套自定义模型服务及协议适配测试。"""
import json

import pytest

from config.llm_config import PROVIDER_CONFIG, list_providers
from llm.protocol_llm import (
    anthropic_messages,
    gemini_contents,
    parse_anthropic_response,
    parse_gemini_response,
)
from utils import custom_providers


@pytest.fixture(autouse=True)
def isolated_custom_providers(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_providers, "CUSTOM_PROVIDERS_PATH", str(tmp_path / "services.json"))
    monkeypatch.setattr(custom_providers, "ENV_PATH", str(tmp_path / ".env"))
    from utils import prefs
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))


def test_removed_cloud_vendors_are_not_selectable():
    assert {"deepseek", "qwen", "zhipu"}.isdisjoint(list_providers())
    assert set(list_providers()) == {"ollama", "lmstudio"}
    assert set(PROVIDER_CONFIG) == {"ollama", "lmstudio"}


def test_custom_service_keeps_secret_out_of_metadata_and_supports_multiple_models(tmp_path):
    service = custom_providers.create_service({
        "name": "Work gateway",
        "protocol": "openai",
        "base_url": "https://api.example.test/v1",
        "api_key": "secret-token",
        "models": ["model-a", "model-b"],
        "default_model": "model-b",
    })

    metadata = json.loads((tmp_path / "services.json").read_text())
    assert "secret-token" not in json.dumps(metadata)
    assert custom_providers.get_api_key(service["id"]) == "secret-token"
    assert service["models"] == ["model-a", "model-b"]
    assert service["default_model"] == "model-b"


def test_custom_service_update_preserves_key_when_key_is_omitted():
    service = custom_providers.create_service({
        "name": "Gateway", "protocol": "openai",
        "base_url": "https://api.example.test/v1", "api_key": "keep-me",
        "models": ["m1"], "default_model": "m1",
    })

    updated = custom_providers.update_service(service["id"], {
        "name": "Updated", "protocol": "anthropic",
        "base_url": "https://gateway.example.test",
        "models": ["claude-model"], "default_model": "claude-model",
    })

    assert updated["name"] == "Updated"
    assert custom_providers.get_api_key(service["id"]) == "keep-me"


def test_custom_service_cannot_be_saved_without_a_model():
    with pytest.raises(ValueError, match="请至少添加一个模型"):
        custom_providers.create_service({
            "name": "Gateway", "protocol": "openai",
            "base_url": "https://api.example.test/v1", "models": [],
        })


def test_legacy_single_custom_settings_migrate_and_repoint_saved_selection(monkeypatch):
    from config.settings import settings
    from utils import prefs

    monkeypatch.setattr(settings, "CUSTOM_API_BASE_URL", "https://legacy.example.test/v1")
    monkeypatch.setattr(settings, "CUSTOM_API_KEY", "legacy-key")
    monkeypatch.setattr(settings, "CUSTOM_API_MODEL", "")
    prefs.save_prefs("custom", "", {"custom": "legacy-model"})

    migrated = custom_providers.migrate_legacy_custom_api()

    assert migrated and migrated["protocol"] == "openai"
    assert migrated["models"] == ["legacy-model"]
    assert custom_providers.get_api_key(migrated["id"]) == "legacy-key"
    assert prefs.load_prefs()["provider"] == f"custom:{migrated['id']}"
    assert prefs.load_prefs()["model"] == "legacy-model"


def test_existing_legacy_service_repoints_old_custom_provider_preference():
    from utils import prefs

    prefs.save_prefs("custom", "", {"custom": "saved-model"})
    custom_providers._write_services([{
        "id": "legacy", "name": "Legacy", "protocol": "openai",
        "base_url": "https://legacy.example.test/v1",
        "models": ["saved-model"], "default_model": "saved-model",
    }])

    custom_providers.migrate_legacy_custom_api()

    assert prefs.load_prefs()["provider"] == "custom:legacy"
    assert prefs.load_prefs()["model"] == "saved-model"


def test_anthropic_messages_convert_system_tools_and_tool_results():
    payload = anthropic_messages(
        [
            {"role": "system", "content": "Be precise"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "found"},
        ],
        [{"name": "lookup", "description": "Search", "parameters": {"type": "object"}}],
        "claude-model", 0.2,
    )

    assert payload["system"] == "Be precise"
    assert payload["messages"][0]["content"][0]["type"] == "tool_use"
    assert payload["messages"][1]["content"][0]["type"] == "tool_result"
    assert payload["tools"][0]["input_schema"]["type"] == "object"


def test_gemini_contents_convert_roles_tools_and_function_results():
    payload = gemini_contents(
        [
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Find X"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call-2", "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "call-2", "content": "found"},
        ],
        [{"name": "lookup", "parameters": {"type": "object"}}],
        0.3,
    )

    assert payload["systemInstruction"]["parts"][0]["text"] == "Be concise"
    assert payload["contents"][0]["role"] == "user"
    assert payload["contents"][1]["parts"][0]["functionCall"]["name"] == "lookup"
    assert payload["contents"][2]["parts"][0]["functionResponse"]["name"] == "lookup"
    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "lookup"


def test_protocol_responses_normalize_text_tool_calls_and_usage():
    anthropic = parse_anthropic_response({
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    gemini = parse_gemini_response({
        "candidates": [{"content": {"parts": [
            {"text": "hello"},
            {"functionCall": {"name": "lookup", "args": {"q": "x"}}},
        ]}}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 7},
    })

    assert anthropic.content == gemini.content == "hello"
    assert anthropic.tool_calls[0].arguments == gemini.tool_calls[0].arguments == {"q": "x"}
    assert anthropic.usage["total_tokens"] == gemini.usage["total_tokens"] == 7
