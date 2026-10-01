"""LLM 工厂：根据 provider 名称创建 LLM 实例。

这是支持"尽可能多 LLM"要求的核心。
所有 OpenAI 兼容协议的提供商复用 OpenAICompatibleLLM。

性能说明：openai 包导入约 2.4 秒，因此延迟到 create() 时才导入，
避免拖慢页面首屏加载。
"""
from typing import Optional

from config.settings import settings
from config.llm_config import PROVIDER_CONFIG, get_provider_config
from llm.base import BaseLLM
from utils.logger import get_logger

logger = get_logger(__name__)

# LLM 实例缓存：同一 provider+model 复用客户端，避免每次对话重复创建
# 与重复触发本地模型自动发现（/v1/models）。仅在模型名确定时缓存；
# 修改 .env 后按设置页提示重启应用即可生效。
_llm_cache: dict[tuple[str, str], "BaseLLM"] = {}


def _cache_key(provider: str, model: str) -> tuple[str, str]:
    return (provider, model)


def normalize_base_url(url: str) -> str:
    """规范化 OpenAI 兼容 Base URL。

    - 去掉尾部多余路径（/chat/completions、/completions、/chat）
    - 不以 /v1 结尾时自动补 /v1
    例如：https://x.com → https://x.com/v1
          https://x.com/v1/chat/completions → https://x.com/v1
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    for suffix in ("/chat/completions", "/completions", "/chat"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    if not base.endswith("/v1"):
        base += "/v1"
    return base


class LLMFactory:
    """LLM 工厂，统一管理多提供商。"""

    @staticmethod
    def create(provider: Optional[str] = None, model: Optional[str] = None) -> BaseLLM:
        """创建指定提供商的 LLM 实例。

        Args:
            provider: 提供商名称 (openai/zhipu/qwen/deepseek/ollama/lmstudio)
            model: 模型名，留空用默认；本地服务空模型会自动发现

        Returns:
            BaseLLM 实例
        """
        provider = (provider or settings.DEFAULT_LLM_PROVIDER).lower()
        config = get_provider_config(provider)
        is_local = config.get("local", False)
        # model=None/"" 时：云端用默认模型，本地空串则触发自动发现
        if model:
            chosen_model = model
        elif is_local and provider == "lmstudio":
            chosen_model = settings.LMSTUDIO_MODEL  # 空串 = 自动发现
        elif is_local and provider == "ollama":
            chosen_model = model or settings.OLLAMA_MODEL
        else:
            chosen_model = config["default_model"]

        # 命中缓存则复用（仅模型名确定时；custom 不缓存——URL/Key 可能随时变）
        key = _cache_key(provider, chosen_model)
        if provider != "custom" and chosen_model and key in _llm_cache:
            return _llm_cache[key]

        # 延迟导入，避免首屏加载 openai（约 2.4s）
        from llm.openai_llm import OpenAICompatibleLLM

        if provider == "openai":
            llm = OpenAICompatibleLLM(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL or config["base_url"],
                model=chosen_model,
                provider=provider,
            )
        elif provider == "zhipu":
            llm = OpenAICompatibleLLM(
                api_key=settings.ZHIPU_API_KEY,
                base_url=config["base_url"],
                model=chosen_model,
                provider=provider,
            )
        elif provider == "qwen":
            llm = OpenAICompatibleLLM(
                api_key=settings.DASHSCOPE_API_KEY,
                base_url=config["base_url"],
                model=chosen_model,
                provider=provider,
            )
        elif provider == "deepseek":
            llm = OpenAICompatibleLLM(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=config["base_url"],
                model=chosen_model,
                provider=provider,
            )
        elif provider == "ollama":
            llm = OpenAICompatibleLLM(
                api_key="ollama",
                base_url=settings.OLLAMA_BASE_URL.rstrip("/") + "/v1",
                model=chosen_model or settings.OLLAMA_MODEL,
                provider=provider,
                is_local=True,
            )
        elif provider == "lmstudio":
            llm = OpenAICompatibleLLM(
                api_key="lm-studio",
                base_url=settings.LMSTUDIO_BASE_URL.rstrip("/") + "/v1",
                model=chosen_model,  # 空串时构造函数自动发现已加载模型
                provider=provider,
                is_local=True,
            )
        elif provider == "custom":
            # 自定义 OpenAI 兼容 API：URL/Key/模型名由用户运行时填写
            base_url = normalize_base_url(settings.CUSTOM_API_BASE_URL)
            if not base_url:
                raise ValueError(
                    "自定义 API 未配置 Base URL。请在侧边栏选择「自定义 API」后填写。"
                )
            if not chosen_model:
                raise ValueError(
                    "自定义 API 未配置模型名。请在侧边栏「模型」处填写。"
                )
            llm = OpenAICompatibleLLM(
                api_key=settings.CUSTOM_API_KEY or "local",
                base_url=base_url,
                model=chosen_model,
                provider=provider,
            )
        else:
            raise ValueError(f"未知提供商: {provider}")

        # 仅缓存模型名确定的实例（custom 不缓存，避免 Key/URL 变更后仍连旧地址）
        if chosen_model and provider != "custom":
            _llm_cache[key] = llm
        return llm

    @staticmethod
    def list_available() -> list[str]:
        """返回 ping 成功的提供商列表，供 UI 下拉。

        只有配置了 API Key 的提供商才会被检测。
        Ollama/LM Studio 无需 key，直接探测本地端口。
        """
        available = []
        for provider, config in PROVIDER_CONFIG.items():
            env_key = config.get("env_key")
            if env_key is None:
                # 本地服务：直接探测
                try:
                    llm = LLMFactory.create(provider)
                    if llm.ping():
                        available.append(provider)
                except Exception:
                    pass
                continue

            # 云端：必须配置 key
            key_value = getattr(settings, env_key, "")
            if not key_value:
                continue
            try:
                llm = LLMFactory.create(provider)
                if llm.ping():
                    available.append(provider)
            except Exception as e:
                logger.debug(f"{provider} ping 失败: {e}")
        return available

    @staticmethod
    def get_models(provider: str) -> list[str]:
        """获取指定提供商的可用模型列表。

        本地动态服务（LM Studio/Ollama）实时查询已安装/已加载模型，
        查询失败回退静态配置；云端返回静态配置。
        """
        config = get_provider_config(provider)
        is_dynamic = config.get("dynamic_models") or provider == "ollama"

        if is_dynamic:
            # LM Studio：查询当前已加载模型；Ollama：查询本地已安装模型
            try:
                llm = LLMFactory.create(provider)
                remote = llm.list_models()
                if remote:
                    return remote
            except Exception as e:
                logger.debug(f"{provider} 动态模型发现失败: {e}")
            # 回退静态列表（列表可能为空，UI 层会提示手动输入）
            models = config.get("models", [])
            return models if models else ([config["default_model"]] if config.get("default_model") else [])

        models = config.get("models", [])
        if not models and config["default_model"]:
            return [config["default_model"]]
        return models

    @staticmethod
    def health_check(provider: str, model: Optional[str] = None) -> dict:
        """对指定提供商做详细健康检查。"""
        try:
            llm = LLMFactory.create(provider, model or None)
            return llm.health_check()
        except Exception as e:
            return {
                "reachable": False,
                "models_loaded": False,
                "model_ready": False,
                "available_models": [],
                "detail": f"初始化失败: {e}",
            }
