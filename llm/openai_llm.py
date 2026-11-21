"""OpenAI 兼容协议实现。

智谱/通义/DeepSeek/Ollama/LM Studio 都提供 OpenAI 兼容接口，
本类可被多个提供商复用，只需传入不同 base_url 和 api_key。
"""
import json
import os
import re
from typing import Iterator, Optional


def _clean_no_proxy() -> None:
    """httpx2（openai 3.x 依赖）无法解析 NO_PROXY 中的 IPv6 字面量（如 [::1]），
    创建客户端时会报 Invalid port；启动时剔除这类条目，避免模型调用全部失败。"""
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key)
        if raw:
            kept = [
                p.strip() for p in raw.split(",")
                if p.strip() and not re.fullmatch(r"\[[^\]]*\]", p.strip())
            ]
            os.environ[key] = ",".join(kept)


_clean_no_proxy()

from openai import (
    OpenAI,
    APIError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

from llm.base import BaseLLM, ChatResponse, ToolCall
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

# 可安全重试的异常：连接失败/超时/限流/服务端错误
_RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


class OpenAICompatibleLLM(BaseLLM):
    """OpenAI 兼容协议的统一实现。

    所有提供 OpenAI 兼容 API 的提供商（智谱/通义/DeepSeek/Ollama/LM Studio）
    都可复用此类，只需传入不同 base_url 和 api_key。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai",
        is_local: bool = False,
    ):
        """初始化。

        Args:
            api_key: API Key（本地服务可传任意占位值）
            base_url: OpenAI 兼容端点（需包含 /v1）
            model: 模型名；本地服务传空字符串时自动发现已加载模型
            provider: 提供商标识
            is_local: 是否为本地推理服务（决定健康检查严格度）
        """
        super().__init__(model=model, provider=provider)
        self.api_key = api_key or "local"
        self.base_url = base_url.rstrip("/")
        self.is_local = is_local
        # 某些提供商不支持 extra_body（如 enable_thinking），失败一次后不再发送
        self._drop_extra_body = False
        # 先创建对话 client（超时 60s 留给长推理），再做模型自动发现。
        # max_retries=0：重试统一由 chat 上的 @retry 负责，
        # 否则 SDK 默认重试 2 次 × 装饰器 3 次 = 最多 9 次请求，成倍消耗配额
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=60.0,
            max_retries=0,
        )
        # 本地服务 model 为空时延迟自动发现（短超时探测，不阻塞页面）
        if is_local and not self.model:
            discovered = self._discover_model()
            if discovered:
                self.model = discovered
                logger.info(f"[{provider}] 自动发现已加载模型: {discovered}")

    @retry(max_attempts=3, backoff=1.5, exceptions=_RETRYABLE_ERRORS)
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_body: Optional[dict] = None,
    ) -> ChatResponse:
        """同步对话。

        Args:
            max_tokens: 输出上限。本地模型（LM Studio/Ollama）默认输出很短（约 512
                tokens），生成报告会被截断；默认给 4096，报告生成可传更大值。
            timeout: 单次请求超时（秒）。长报告生成需要大于客户端默认 60 秒。
            extra_body: 额外请求字段（如 {"enable_thinking": False} 关闭推理模型的
                内部思考）。提供商不支持时自动降级为不发送并记住该能力。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if timeout:
            kwargs["timeout"] = timeout
        if extra_body and not self._drop_extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = self._format_tools(tools)

        # 本地服务未加载模型时给出明确指引（避免发出 model="" 的无效请求）
        if self.is_local and not self.model:
            discovered = self._discover_model()
            if discovered:
                self.model = discovered
                kwargs["model"] = discovered
                logger.info(f"[{self.provider}] 运行时发现模型: {discovered}")
            else:
                msg = (
                    f"本地服务 {self.base_url} 已启动，但未加载任何模型。"
                    "请在 LM Studio/Ollama 中加载模型后重试。"
                )
                logger.error(msg)
                return ChatResponse(content=msg, usage={})

        try:
            resp = self.client.chat.completions.create(**kwargs)
            return self._parse_response(resp)
        except RateLimitError as e:
            # 分钟级限流（rpm/quota/rate limit）重试无意义且浪费时间，
            # 直接返回友好提示，让用户稍等或换提供商/模型
            msg = str(e).lower()
            if any(k in msg for k in (
                "rpm", "quota", "exhausted", "rate limit",
                "requests per minute", "tpm", "too many requests",
            )):
                logger.warning(f"[{self.provider}] 检测到限流: {e}")
                return ChatResponse(
                    content=(
                        f"⚠️ API 限流（请求过于频繁）：{e}\n\n"
                        "请稍等 1~2 分钟再试；或在侧边栏更换模型 / 提供商。"
                        "多智能体研究任务会连续调用多次，更容易触发限流。"
                    ),
                    usage={},
                )
            raise  # 其他 429（如瞬时抖动）交给重试装饰器
        except _RETRYABLE_ERRORS:
            raise  # 交给重试装饰器
        except APIError as e:
            # 提供商不认识 extra_body（如 enable_thinking）：去掉后重发一次，
            # 并记住该实例不再发送，避免后续每次调用都多花一个请求。
            if extra_body and "extra_body" in kwargs and getattr(e, "status_code", None) == 400:
                self._drop_extra_body = True
                logger.info(f"[{self.provider}] 不支持 extra_body，后续请求将不再发送")
                kwargs.pop("extra_body", None)
                try:
                    return self._parse_response(self.client.chat.completions.create(**kwargs))
                except APIError as e2:
                    e = e2
            # 本地部分模型不支持 Function Calling：400 且错误与 tools 相关时，
            # 自动降级为纯对话重试一次，避免整个对话不可用
            if (
                getattr(e, "status_code", None) == 400
                and "tools" in kwargs
                and "tool" in str(e).lower()
            ):
                logger.warning(
                    f"[{self.provider}] 模型不支持 Function Calling，降级为纯对话模式"
                )
                try:
                    kwargs.pop("tools", None)
                    resp = self.client.chat.completions.create(**kwargs)
                    response = self._parse_response(resp)
                    if response.content:
                        response.content += (
                            "\n\n（提示：当前模型不支持工具调用，以上为纯模型回答）"
                        )
                    return response
                except APIError as e2:
                    logger.error(f"[{self.provider}] 降级对话仍失败: {e2}")
                    return ChatResponse(content=f"API 调用失败: {e2}", usage={})
            # 400/401/403 等不可重试错误，直接返回错误响应
            logger.error(f"[{self.provider}] 对话失败 ({getattr(e, 'status_code', '?')}): {e}")
            return ChatResponse(content=f"API 调用失败: {e}", usage={})

    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        extra_body: Optional[dict] = None,
    ) -> Iterator[str]:
        """流式输出。"""
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout
        if extra_body and not self._drop_extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = self._format_tools(tools)

        try:
            stream = self.client.chat.completions.create(**kwargs)
        except APIError as e:
            if extra_body and "extra_body" in kwargs and getattr(e, "status_code", None) == 400:
                self._drop_extra_body = True
                logger.info(f"[{self.provider}] 不支持 extra_body，后续请求将不再发送")
                kwargs.pop("extra_body", None)
                stream = self.client.chat.completions.create(**kwargs)
            else:
                raise

        reason = None
        try:
            for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        yield choice.delta.content
                    if choice.finish_reason:
                        reason = choice.finish_reason
            if reason != "stop":
                raise RuntimeError(f"模型流式响应未完整结束（{reason or '连接中断'}）")
        finally:
            stream.close()

    def ping(self) -> bool:
        """健康检查：服务是否可达。"""
        try:
            self.client.models.list()
            return True
        except Exception as e:
            logger.debug(f"[{self.provider}] ping 失败: {e}")
            return False

    def list_models(self) -> list[str]:
        """通过 /v1/models 列出服务端可用模型。"""
        try:
            resp = self.client.models.list()
            return [m.id for m in resp.data]
        except Exception as e:
            logger.debug(f"[{self.provider}] list_models 失败: {e}")
            return []

    def health_check(self) -> dict:
        """三级健康检查。

        本地服务严格区分：服务可达 / 模型已加载 / 指定模型就绪。
        云端服务可达即视为就绪。
        """
        if not self.ping():
            return {
                "reachable": False,
                "models_loaded": False,
                "model_ready": False,
                "available_models": [],
                "detail": (
                    f"无法连接 {self.base_url}，"
                    + ("请确认 LM Studio/Ollama 已启动并开启本地服务" if self.is_local
                       else "请检查 API Key 和网络")
                ),
            }

        if not self.is_local:
            # 云端：ping 成功即就绪，不再多发一次 /models 请求
            return {
                "reachable": True,
                "models_loaded": True,
                "model_ready": True,
                "available_models": [],
                "detail": f"就绪，当前模型: {self.model}",
            }

        # 本地服务三级检查
        models = self.list_models()
        if not models:
            return {
                "reachable": True,
                "models_loaded": False,
                "model_ready": False,
                "available_models": [],
                "detail": "本地服务已启动，但没有加载任何模型，请在客户端中加载模型",
            }

        if self.model and self.model not in models:
            return {
                "reachable": True,
                "models_loaded": True,
                "model_ready": False,
                "available_models": models,
                "detail": f"模型 {self.model} 未加载，当前已加载: {models}",
            }

        current = self.model or models[0]
        return {
            "reachable": True,
            "models_loaded": True,
            "model_ready": True,
            "available_models": models,
            "detail": f"就绪，当前模型: {current}",
        }

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        try:
            self.client.close()
        except Exception:
            pass

    def _discover_model(self) -> str:
        """自动发现本地服务当前加载的模型。

        使用独立的短超时 client（3s）探测，避免本地服务在监听但
        无响应时阻塞 60 秒；探测失败返回空串由上层降级处理。
        """
        try:
            probe = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=3.0,
                max_retries=0,
            )
            try:
                models = [m.id for m in probe.models.list().data]
            finally:
                probe.close()
            return models[0] if models else ""
        except Exception:
            return ""

    def _format_tools(self, tools: list[dict]) -> list[dict]:
        """格式化工具 schema 为 OpenAI Function Calling 格式。"""
        formatted = []
        for t in tools:
            if t.get("type") == "function":
                formatted.append(t)
            else:
                formatted.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                    },
                })
        return formatted

    def _parse_response(self, resp) -> ChatResponse:
        """解析 OpenAI 响应为统一 ChatResponse。"""
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        return ChatResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            raw=resp,
            finish_reason=getattr(choice, "finish_reason", "") or "",
        )
