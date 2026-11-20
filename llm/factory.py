"""LLM 工厂：根据 provider 名称创建 LLM 实例。

这是支持"尽可能多 LLM"要求的核心。
所有 OpenAI 兼容协议的提供商复用 OpenAICompatibleLLM。

性能说明：openai 包导入约 2.4 秒，因此延迟到 create() 时才导入，
避免拖慢页面首屏加载。
"""
import re
from typing import Optional

from config.settings import settings
from config.llm_config import get_provider_config
from llm.base import BaseLLM
from utils.logger import get_logger

logger = get_logger(__name__)

# LLM 实例缓存：同一 provider+model 复用客户端，避免每次对话重复创建
# 与重复触发本地模型自动发现（/v1/models）。仅在模型名确定时缓存；
# 复用前核对有效 URL/Key，设置页修改配置后立即生效。临时连接不参与缓存。
_llm_cache: dict[tuple[str, str], "BaseLLM"] = {}


def normalize_base_url(url: str) -> str:
    """规范化 OpenAI 兼容 Base URL。

    - 去掉尾部多余路径（/chat/completions、/completions、/chat）
    - 仅在路径中没有版本段时补 /v1
    例如：https://x.com → https://x.com/v1
          https://x.com/v1/chat/completions → https://x.com/v1
          https://open.bigmodel.cn/api/paas/v4 → 原样保留
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    for suffix in ("/chat/completions", "/completions", "/chat"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    # 已有版本段（/v1、/v4、/v1beta…）或 /openai 子路径时原样使用：
    # 盲目追加 /v1 会拼出 .../v4/v1 这类不存在的端点，导致全部请求 404
    if re.search(r"/v\d+[A-Za-z0-9.]*$", base) or base.endswith("/openai"):
        return base
    return base + "/v1"


class LLMFactory:
    """LLM 工厂，统一管理多提供商。"""

    @staticmethod
    def create(provider: Optional[str] = None, model: Optional[str] = None,
               api_key: Optional[str] = None, base_url: Optional[str] = None) -> BaseLLM:
        """创建指定提供商的 LLM 实例。

        Args:
            provider: 提供商名称 (zhipu/qwen/deepseek/custom/ollama/lmstudio)
            model: 模型名，留空用默认；本地服务空模型会自动发现
            api_key: 临时覆盖 API Key（前端刚填写、尚未保存时用于测试连接）
            base_url: 临时覆盖 Base URL

        Returns:
            BaseLLM 实例
        """
        if not provider:
            provider = settings.DEFAULT_LLM_PROVIDER
            try:
                from utils.prefs import load_prefs
                preferred = load_prefs().get("provider")
                if preferred:
                    provider = preferred
            except Exception:
                pass
            if provider == "custom":
                provider = "custom:legacy"
            try:
                get_provider_config(provider)
            except ValueError:
                from config.llm_config import list_providers
                choices = list_providers()
                provider = choices[0] if choices else ""
        provider = (provider or "").lower()
        legacy_custom = provider == "custom"
        config = ({
            "label": "旧版自定义 API", "default_model": settings.CUSTOM_API_MODEL,
            "models": [settings.CUSTOM_API_MODEL] if settings.CUSTOM_API_MODEL else [],
            "base_url": settings.CUSTOM_API_BASE_URL, "protocol": "openai",
            "service_id": "legacy", "custom": True,
        } if legacy_custom else get_provider_config(provider))
        is_local = config.get("local", False)
        # model=None/"" 时：云端用默认模型，本地空串则触发自动发现
        if model:
            chosen_model = model
        elif is_local and provider == "lmstudio":
            chosen_model = settings.LMSTUDIO_MODEL  # 空串 = 自动发现
        elif is_local and provider == "ollama":
            chosen_model = settings.OLLAMA_MODEL
        elif config.get("custom"):
            chosen_model = config["default_model"]
        else:
            chosen_model = config["default_model"]

        override_key = (api_key or "").strip()
        override_url = (base_url or "").strip()
        if config.get("custom"):
            from utils.custom_providers import get_api_key
            protocol = config["protocol"]
            effective_url = override_url or config["base_url"]
            if protocol == "openai":
                effective_url = normalize_base_url(effective_url)
            else:
                effective_url = effective_url.strip().rstrip("/")
                if protocol == "anthropic":
                    for suffix in ("/v1/messages", "/messages", "/v1"):
                        if effective_url.endswith(suffix):
                            effective_url = effective_url[:-len(suffix)].rstrip("/")
                            break
                if protocol == "gemini":
                    effective_url = re.sub(r"/v1beta(?:/.*)?$", "", effective_url)
            effective_key = override_key or get_api_key(config["service_id"]) or "local"
            if not effective_url:
                raise ValueError(
                    "自定义 API 未配置 Base URL，请前往设置页补全。"
                )
            if not chosen_model:
                raise ValueError(
                    "自定义服务尚未配置模型名，请在设置页添加至少一个模型。"
                )
        elif is_local:
            effective_url = normalize_base_url(
                override_url or getattr(settings, f"{provider.upper()}_BASE_URL")
            )
            effective_key = override_key or ("ollama" if provider == "ollama" else "lm-studio")
        else:
            effective_url = normalize_base_url(
                override_url or getattr(settings, config["env_base_url"]) or config["base_url"]
            )
            effective_key = override_key or getattr(settings, config["env_key"]) or "local"

        key = (provider, chosen_model)
        cacheable = bool(chosen_model) and not config.get("custom") and not (override_key or override_url)
        cached = _llm_cache.get(key) if cacheable else None
        if cached and cached.base_url == effective_url and cached.api_key == effective_key:
            return cached

        if config.get("custom") and config["protocol"] != "openai":
            from llm.protocol_llm import CustomProtocolLLM
            llm = CustomProtocolLLM(
                protocol=config["protocol"], api_key=effective_key,
                base_url=effective_url, model=chosen_model, provider=provider,
            )
        else:
            # 延迟导入，避免首屏加载 openai（约 2.4s）
            from llm.openai_llm import OpenAICompatibleLLM
            llm = OpenAICompatibleLLM(
                api_key=effective_key, base_url=effective_url, model=chosen_model,
                provider=provider, is_local=is_local,
            )

        if cacheable:
            _llm_cache[key] = llm
        return llm

    @staticmethod
    def get_models(provider: str) -> list[str]:
        """获取指定提供商的可用对话模型列表。

        本地动态服务（LM Studio/Ollama）实时查询已安装/已加载模型，
        查询失败回退静态配置；云端返回静态配置。结果去重
        （LM Studio 可能对同一模型的不同量化版返回重复 ID），
        并过滤掉向量模型，避免用户误把 embedding 模型当成对话模型。
        """
        _EMBED_KW = ("embed", "bge", "e5-", "gte", "minilm", "sentence")

        def _filter(models: list[str]) -> list[str]:
            picked = [m for m in models if not any(k in m.lower() for k in _EMBED_KW)]
            return list(dict.fromkeys(picked))  # 去重且保序

        config = get_provider_config(provider)
        is_dynamic = config.get("dynamic_models") or provider == "ollama"
        if config.get("custom"):
            return list(config.get("models", []))

        if is_dynamic:
            # LM Studio：查询当前已加载模型；Ollama：查询本地已安装模型
            try:
                llm = LLMFactory.create(provider)
                remote = llm.list_models()
                if remote:
                    return _filter(remote)
            except Exception as e:
                logger.debug(f"{provider} 动态模型发现失败: {e}")
            # 回退静态列表（列表可能为空，UI 层会提示手动输入）
            models = config.get("models", [])
            if models:
                return _filter(models)
            return ([config["default_model"]] if config.get("default_model") else [])

        models = config.get("models", [])
        if not models and config["default_model"]:
            return [config["default_model"]]
        return _filter(models)

    @staticmethod
    def health_check(provider: str, model: Optional[str] = None,
                     api_key: Optional[str] = None, base_url: Optional[str] = None) -> dict:
        """对指定提供商做详细健康检查（支持临时 Key/URL，供前端测试连接）。"""
        try:
            llm = LLMFactory.create(provider, model or None, api_key=api_key, base_url=base_url)
            return llm.health_check()
        except Exception as e:
            return {
                "reachable": False,
                "models_loaded": False,
                "model_ready": False,
                "available_models": [],
                "detail": f"初始化失败: {e}",
            }
