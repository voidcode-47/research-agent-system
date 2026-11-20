"""本地模型提供商与自定义服务的运行时配置。"""

PROVIDER_CONFIG: dict[str, dict] = {
    "ollama": {
        "label": "Ollama (本地)",
        "default_model": "qwen2.5:7b",
        "models": ["qwen2.5:7b", "llama3.1:8b", "qwen2.5:14b"],
        "context_window": 32768,
        "supports_tools": True,
        "base_url": "http://localhost:11434/v1",
        "env_key": None,
        "local": True,
    },
    "lmstudio": {
        "label": "LM Studio (本地)",
        "default_model": "",
        "models": [],
        "context_window": 32768,
        "supports_tools": True,
        "base_url": "http://localhost:1234/v1",
        "env_key": None,
        "local": True,
        "dynamic_models": True,
    },
}


def get_provider_config(provider: str) -> dict:
    """获取本地或某个已配置自定义服务的运行时信息。"""
    provider = (provider or "").lower()
    if provider.startswith("custom:"):
        from utils.custom_providers import get_service_config
        return get_service_config(provider)
    if provider not in PROVIDER_CONFIG:
        raise ValueError(f"未知或已移除的提供商: {provider}，可选: {list(PROVIDER_CONFIG)}")
    return PROVIDER_CONFIG[provider]


def list_providers() -> list[str]:
    """返回当前可选的本地和自定义服务标识。"""
    from utils.custom_providers import list_services
    local = list(PROVIDER_CONFIG)
    return local + [f"custom:{service['id']}" for service in list_services()]
