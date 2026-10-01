"""LLM 抽象基类与统一响应数据结构。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class ToolCall:
    """工具调用请求。"""
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    """统一的对话响应，屏蔽各家 API 差异。"""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    raw: Any = None  # 原始响应，便于调试

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class BaseLLM(ABC):
    """所有 LLM 提供商必须实现的统一接口。"""

    def __init__(self, model: str, provider: str = ""):
        self.model = model
        self.provider = provider

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> ChatResponse:
        """同步对话，支持 Function Calling。"""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> Iterator[str]:
        """流式输出，UI 实时显示。"""

    @abstractmethod
    def ping(self) -> bool:
        """健康检查，验证 API 可达且 key 有效。"""

    def health_check(self) -> dict:
        """详细健康检查，区分三级状态。

        Returns:
            {
                "reachable": bool,       # 服务是否可达
                "models_loaded": bool,   # 是否有可用模型
                "model_ready": bool,     # 当前指定模型是否可推理
                "available_models": list[str],
                "detail": str,           # 人类可读状态说明
            }
        """
        reachable = self.ping()
        if reachable:
            return {
                "reachable": True,
                "models_loaded": True,
                "model_ready": True,
                "available_models": [],
                "detail": f"就绪，当前模型: {self.model}",
            }
        return {
            "reachable": False,
            "models_loaded": False,
            "model_ready": False,
            "available_models": [],
            "detail": "服务不可达，请检查 API Key、网络或本地服务",
        }

    def list_models(self) -> list[str]:
        """列出远端可用模型。云端提供商默认返回空（用静态配置）。"""
        return []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """默认不实现，由 EmbeddingProvider 处理。"""
        raise NotImplementedError(f"{self.provider} 不直接提供 embedding")
