"""全局配置，从 .env 加载。所有 key 可选，留空则该提供商不可用。"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置单例，所有模块的 .env 入口。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM 默认提供商
    DEFAULT_LLM_PROVIDER: str = "openai"
    DEFAULT_EMBEDDING_PROVIDER: str = "openai"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # 智谱 GLM
    ZHIPU_API_KEY: str = ""

    # 通义千问 (DashScope)
    DASHSCOPE_API_KEY: str = ""

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""

    # 自定义 OpenAI 兼容 API（由用户在侧边栏/设置页填写，可连接任意兼容网关）
    CUSTOM_API_BASE_URL: str = ""
    CUSTOM_API_KEY: str = ""
    CUSTOM_API_MODEL: str = ""

    # Ollama 本地模型
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # LM Studio 本地模型（默认 OpenAI 兼容端点 http://localhost:1234/v1）
    LMSTUDIO_BASE_URL: str = "http://localhost:1234"
    LMSTUDIO_MODEL: str = ""  # 留空则自动使用 LM Studio 当前已加载的模型
    # 留空则自动从已加载模型中识别（id 含 embed/bge/e5 等关键词）
    LMSTUDIO_EMBEDDING_MODEL: str = ""
    # Ollama embedding 模型，留空用默认 nomic-embed-text
    OLLAMA_EMBEDDING_MODEL: str = ""

    # RAG 参数
    # RAG 切片与检索参数（按中文论文场景折算：
    # 800 字符 ≈ 400~500 tokens 语义块 + 100 字符重叠保衔接；Top-8 召回更完整）
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 100
    TOP_K: int = 8

    # 安全参数
    MAX_REACT_ITERATIONS: int = 10
    MAX_TOKEN_BUDGET: int = 8000

    # 存储
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"
    UPLOADS_DIR: str = "./data/uploads"
    CACHE_DIR: str = "./data/cache"

    def ensure_dirs(self) -> None:
        """确保数据目录存在。"""
        for d in [self.CHROMA_PERSIST_DIR, self.UPLOADS_DIR, self.CACHE_DIR]:
            Path(d).mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
