"""工具基类与注册表。

定义统一接口，用注册表模式让 Agent 自动发现可用工具。
"""
from abc import ABC, abstractmethod
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class BaseTool(ABC):
    """工具抽象基类。

    Attributes:
        dangerous: 标记该工具可能执行破坏性操作（如 shell/终端/文件删除）。
            为 True 时，ReActAgent 在执行前会用 SafetyGuard.check_destructive
            做运行时硬拦截（与 config.prompts.SAFETY_CONSTRAINTS 提示配合）。
            默认 False，搜索/抓取等只读工具保持不变。
    """

    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    dangerous: bool = False

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """执行工具，返回字符串观测结果。"""

    def to_openai_schema(self) -> dict:
        """转为 OpenAI Function Calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __call__(self, **kwargs) -> str:
        """便捷调用。"""
        try:
            return self.execute(**kwargs)
        except Exception as e:
            logger.error(f"工具 {self.name} 执行失败: {e}")
            return f"工具执行失败: {e}"


# 全局工具注册表
TOOL_REGISTRY: dict[str, BaseTool] = {}


def register_tool(tool: BaseTool) -> BaseTool:
    """注册工具到全局表。"""
    if not tool.name:
        raise ValueError("工具必须有 name 属性")
    TOOL_REGISTRY[tool.name] = tool
    logger.debug(f"注册工具: {tool.name}")
    return tool


def get_tool(name: str) -> Optional[BaseTool]:
    """获取工具。"""
    return TOOL_REGISTRY.get(name)


def list_tools() -> list[str]:
    """列出所有已注册工具名。"""
    return list(TOOL_REGISTRY.keys())


def get_tools_schema(tool_names: list[str]) -> list[dict]:
    """获取指定工具的 OpenAI schema 列表。"""
    schemas = []
    for name in tool_names:
        tool = TOOL_REGISTRY.get(name)
        if tool:
            schemas.append(tool.to_openai_schema())
    return schemas
