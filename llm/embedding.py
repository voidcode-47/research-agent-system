"""Embedding 提供商。

独立于对话 LLM，因为 DeepSeek 无 embedding 服务，
需回退到其他提供商。回退链：智谱→通义→Ollama→LM Studio。
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

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        """初始化 embedding 提供商。

        Args:
            provider: 指定提供商，留空则按回退链自动选择
            model: 指定模型名（须同时指定 provider），
                   知识库场景在设置页配置了 embedding 模型时优先用它
        """
        self._provider = provider
        self._forced_model = (model or "").strip()
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

        if config.get("custom"):
            # 自定义 API：Key/URL 由用户运行时填写，字段名与 LLMFactory.create 对齐
            api_key = settings.CUSTOM_API_KEY
            base_url = settings.CUSTOM_API_BASE_URL
        else:
            env_key = config.get("env_key")
            api_key = getattr(settings, env_key, "") if env_key else ""
            # 云端端点允许通过 .env 的 {PROVIDER}_BASE_URL 覆盖为任意兼容网关
            # （与 LLMFactory.create 保持一致），否则对话走了自建网关、
            # embedding 仍打官方地址，两边模型不一致
            base_url = config["base_url"]
            env_base = getattr(settings, f"{provider.upper()}_BASE_URL", "")
            if env_base:
                base_url = env_base
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
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
            # 回退链：免费本地优先，再到云端（zhipu → qwen）
            candidates = ["lmstudio", "ollama", "zhipu", "qwen"]

        last_error = None
        first_real_error = None  # 第一个"真实故障"（连接失败/API 错误），优先向用户展示
        for p in candidates:
            config = PROVIDER_CONFIG.get(p)
            if not config:
                continue

            # 云端必须配置 key
            env_key = config.get("env_key")
            if env_key:
                api_key = getattr(settings, env_key, "")
                if not api_key:
                    last_error = f"{p} 云端未配置 API Key（{env_key} 为空）"
                    continue

            try:
                client = self._build_client(p, config)
                # 用户指定了 provider+model（知识库场景配置）→ 直接用指定模型；
                # 否则自动探测/取配置的 embedding 模型
                forced = self._forced_model if p == self._provider else ""
                embed_model = forced or self._resolve_embedding_model(p, config, client)
                if not embed_model:
                    logger.debug(f"embedding {p} 未配置/发现 embedding 模型，跳过")
                    last_error = f"{p} 未发现 embedding 模型"
                    continue

                # 测试连通性
                client.embeddings.create(model=embed_model, input="test")
                self._client = client
                self._model = embed_model
                self._provider = p
                logger.info(f"Embedding 提供商: {p} / {embed_model}")
                return
            except Exception as e:
                base = config.get("base_url", "")
                if p in ("lmstudio", "ollama"):
                    err = (
                        f"{p} 本地服务（{base}）连接失败：{e}。"
                        f"请确认 {p} 已启动本地 API 服务器并加载了 embedding 模型"
                    )
                else:
                    err = f"{p} 云端 API 不可用：{e}"
                if first_real_error is None:
                    first_real_error = err
                last_error = err
                logger.debug(f"embedding {p} 不可用: {e}")
                continue

        # 优先展示"真实故障"（本地服务没开/云端报错），
        # 而不是最后一个"未配置 Key"类提示，避免误导用户
        detail = first_real_error or last_error
        raise RuntimeError(
            f"无可用 embedding 提供商。最后尝试结果：{detail}"
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
