"""Embedding 提供商。

独立于对话 LLM，因为 DeepSeek 无 embedding 服务，
需回退到其他提供商。回退链：OpenAI→智谱→Ollama→LM Studio。
本地 embedding 模型（Ollama/LM Studio）优先于付费云端。
"""
from typing import Optional

from config.settings import settings
from config.llm_config import PROVIDER_CONFIG, get_provider_config
from utils.logger import get_logger

logger = get_logger(__name__)

# embedding 模型 id 的识别关键词（用于 LM Studio 自动探测）
_EMBED_MODEL_KEYWORDS = ("embed", "bge", "e5", "gte", "minilm", "sentence")


class EmbeddingProvider:
    """Embedding 提供商，支持自动回退。"""

    def __init__(self, provider: Optional[str] = None):
        """初始化 embedding 提供商。

        Args:
            provider: 指定提供商，留空则按回退链自动选择
        """
        self._provider = provider
        self._client = None
        self._model = None
        self._dim = None
        self._init_client()

    def _build_client(self, provider: str, config: dict):
        """构建某提供商的 OpenAI 兼容客户端。

        必须显式设置 timeout：openai SDK 默认超时 600 秒，
        本地服务无响应时会导致页面每次 rerun 都被阻塞数十秒。
        本地服务用 3 秒探测超时，云端用 10 秒。
        """
        from openai import OpenAI

        if provider == "ollama":
            return OpenAI(
                api_key="ollama",
                base_url=settings.OLLAMA_BASE_URL.rstrip("/") + "/v1",
                timeout=3.0,
                max_retries=0,
            )
        if provider == "lmstudio":
            return OpenAI(
                api_key="lm-studio",
                base_url=settings.LMSTUDIO_BASE_URL.rstrip("/") + "/v1",
                timeout=3.0,
                max_retries=0,
            )

        env_key = config.get("env_key")
        api_key = getattr(settings, env_key, "") if env_key else ""
        return OpenAI(
            api_key=api_key,
            base_url=config["base_url"],
            timeout=10.0,
            max_retries=1,
        )

    def _resolve_embedding_model(self, provider: str, config: dict, client) -> str:
        """确定使用的 embedding 模型名。"""
        if provider == "lmstudio":
            # 优先用显式配置
            configured = getattr(settings, "LMSTUDIO_EMBEDDING_MODEL", "")
            if configured:
                return configured
            # 自动从已加载模型中识别 embedding 模型
            try:
                models = [m.id for m in client.models.list().data]
                for mid in models:
                    if any(kw in mid.lower() for kw in _EMBED_MODEL_KEYWORDS):
                        return mid
            except Exception:
                pass
            return ""

        if provider == "ollama":
            return getattr(settings, "OLLAMA_EMBEDDING_MODEL", "") or config.get("embedding_model", "")
        return config.get("embedding_model", "")

    def _init_client(self):
        """初始化 embedding 客户端，按回退链选择。"""
        if self._provider:
            candidates = [self._provider]
        else:
            # 回退链：免费本地优先，再到云端
            candidates = ["lmstudio", "ollama", "openai", "zhipu"]

        last_error = None
        for p in candidates:
            config = PROVIDER_CONFIG.get(p)
            if not config:
                continue

            # 云端必须配置 key
            env_key = config.get("env_key")
            if env_key:
                api_key = getattr(settings, env_key, "")
                if not api_key:
                    continue

            try:
                client = self._build_client(p, config)
                embed_model = self._resolve_embedding_model(p, config, client)
                if not embed_model:
                    logger.debug(f"embedding {p} 未配置/发现 embedding 模型，跳过")
                    continue

                # 测试连通性
                client.embeddings.create(model=embed_model, input="test")
                self._client = client
                self._model = embed_model
                self._provider = p
                logger.info(f"Embedding 提供商: {p} / {embed_model}")
                return
            except Exception as e:
                last_error = e
                logger.debug(f"embedding {p} 不可用: {e}")
                continue

        raise RuntimeError(
            f"无可用 embedding 提供商（最后错误: {last_error}）。"
            "请配置云端 API Key，或在 Ollama/LM Studio 中加载 embedding 模型"
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成 embedding。"""
        if not texts:
            return []
        results = []
        batch_size = 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            results.extend([d.embedding for d in resp.data])
        return results

    def embed_one(self, text: str) -> list[float]:
        """单个文本 embedding。"""
        resp = self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding

    def dimension(self) -> int:
        """返回 embedding 维度。"""
        if self._dim is None:
            self._dim = len(self.embed_one("test"))
        return self._dim

    @property
    def provider_name(self) -> str:
        return self._provider or ""

    @property
    def model_name(self) -> str:
        return self._model or ""
