"""全局配置，从 .env 加载。所有 key 可选，留空则该提供商不可用。"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置单例，所有模块的 .env 入口。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM 默认提供商（云端默认 DeepSeek；本地可选 ollama/lmstudio）
    DEFAULT_LLM_PROVIDER: str = "deepseek"

    # 智谱 GLM
    ZHIPU_API_KEY: str = ""
    ZHIPU_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"

    # 通义千问 (DashScope)
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    # 自定义 OpenAI 兼容 API（由用户在设置页填写，可连接任意兼容网关）
    CUSTOM_API_BASE_URL: str = ""
    CUSTOM_API_KEY: str = ""
    CUSTOM_API_MODEL: str = ""

    # Ollama 本地模型
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # LM Studio 本地模型（默认 OpenAI 兼容端点 http://localhost:1234/v1）
    LMSTUDIO_BASE_URL: str = "http://localhost:1234"
    LMSTUDIO_MODEL: str = ""  # 留空则自动使用 LM Studio 当前已加载的模型

    # 论文工作台数据目录（papers/index.json 与各 paper_id 子目录）
    PAPER_DATA_DIR: str = "./data/papers"

    # 论文后台任务并发与队列上限（启动时生效，修改后需重启）
    PAPER_TASK_WORKERS: int = Field(default=3, ge=1, le=16)
    PAPER_MAX_ACTIVE_TASKS: int = Field(default=12, ge=1, le=100)
    # 已结束任务在内存中的保留分钟数；0 表示不按时间清理，仅受数量硬上限
    PAPER_TASK_RETENTION_MINUTES: int = Field(default=1440, ge=0, le=525600)

    # 每日 LLM 调用配额（按 key 计数，论文任务默认 local）
    DAILY_QUOTA: int = Field(default=30, ge=0)

    # 管理员账号：可修改模型且不受每日配额限制
    ADMIN_USERNAME: str = "admin"
    # 留空则首次启动自动生成随机密码并写入 data/admin_credentials.txt
    ADMIN_PASSWORD: str = ""

    def ensure_dirs(self) -> None:
        """确保数据目录存在。"""
        Path(self.PAPER_DATA_DIR).mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
