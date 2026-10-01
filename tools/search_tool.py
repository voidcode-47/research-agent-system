"""DuckDuckGo 搜索工具。"""
import json
from typing import Any

from tools.base import BaseTool, register_tool
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)


class DuckDuckGoSearchTool(BaseTool):
    """DuckDuckGo 搜索，免费无需 API Key。"""

    name = "web_search"
    description = "搜索互联网获取最新信息。输入搜索关键词，返回相关网页标题、摘要和链接。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 5",
            },
        },
        "required": ["query"],
    }

    def __init__(self):
        self._ddgs = None

    def _get_ddgs(self):
        """懒加载 duckduckgo_search。"""
        if self._ddgs is not None:
            return self._ddgs
        from duckduckgo_search import DDGS
        self._ddgs = DDGS()
        return self._ddgs

    @retry(max_attempts=2, backoff=1.5)
    def execute(self, query: str, max_results: int = 5) -> str:
        """执行搜索。"""
        try:
            ddgs = self._get_ddgs()
            results = list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            logger.warning(f"搜索失败，尝试备用: {e}")
            # 重新初始化
            self._ddgs = None
            ddgs = self._get_ddgs()
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"未找到关于 '{query}' 的搜索结果"

        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            formatted.append(f"{i}. {title}\n   {body}\n   来源: {href}")

        return f"搜索 '{query}' 的结果:\n\n" + "\n\n".join(formatted)


# 自动注册
register_tool(DuckDuckGoSearchTool())
