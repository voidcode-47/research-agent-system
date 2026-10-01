"""各 LLM 提供商配置表，集中维护模型名/上下文窗口/能力。"""

PROVIDER_CONFIG: dict[str, dict] = {
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
        "embedding_model": "text-embedding-3-small",
        "context_window": 128000,
        "supports_tools": True,
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "default_model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4", "glm-4-air"],
        "embedding_model": "embedding-3",
        "context_window": 128000,
        "supports_tools": True,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "ZHIPU_API_KEY",
    },
    "qwen": {
        "label": "通义千问",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
        "embedding_model": "text-embedding-v3",
        "context_window": 131072,
        "supports_tools": True,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "embedding_model": None,
        "context_window": 64000,
        "supports_tools": True,
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "custom": {
        "label": "自定义 API (OpenAI 兼容)",
        # URL/Key/模型名均由用户运行时填写（侧边栏/设置页），不预设
        "default_model": "",
        "models": [],
        "embedding_model": "",
        "context_window": 128000,
        "supports_tools": True,
        "base_url": "",
        "env_key": None,
        "custom": True,
    },
    "ollama": {
        "label": "Ollama (本地)",
        "default_model": "qwen2.5:7b",
        "models": ["qwen2.5:7b", "llama3.1:8b", "qwen2.5:14b"],
        "embedding_model": "nomic-embed-text",
        "context_window": 32768,
        "supports_tools": True,
        "base_url": "http://localhost:11434/v1",
        "env_key": None,
        "local": True,
    },
    "lmstudio": {
        "label": "LM Studio (本地)",
        # 模型由 LM Studio 动态加载，留空表示运行时通过 /v1/models 自动发现
        "default_model": "",
        "models": [],
        # embedding 模型需在 LM Studio 中手动加载（如 nomic-embed-text/bge）
        "embedding_model": "",
        "context_window": 32768,
        "supports_tools": True,
        "base_url": "http://localhost:1234/v1",
        "env_key": None,
        "local": True,
        "dynamic_models": True,
    },
}


def get_provider_config(provider: str) -> dict:
    """获取指定提供商的配置。"""
    provider = provider.lower()
    if provider not in PROVIDER_CONFIG:
        raise ValueError(f"未知提供商: {provider}，可选: {list(PROVIDER_CONFIG.keys())}")
    return PROVIDER_CONFIG[provider]


def list_providers() -> list[str]:
    """返回所有支持的提供商名称。"""
    return list(PROVIDER_CONFIG.keys())
