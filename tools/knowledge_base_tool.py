"""知识库查询工具，供 Agent 调用。"""
from typing import Optional

from tools.base import BaseTool, register_tool
from tools.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)


class KnowledgeBaseTool(BaseTool):
    """知识库检索工具。"""

    name = "knowledge_search"
    description = "检索本地知识库，查找相关文档。输入查询关键词，返回相关文档片段。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词",
            },
            "collection": {
                "type": "string",
                "description": "知识库名称，留空用默认",
            },
            "k": {
                "type": "integer",
                "description": "返回数量，默认 3",
            },
        },
        "required": ["query"],
    }

    def __init__(self, vector_store: VectorStore, default_collection: str = "default"):
        self._vs = vector_store
        self._default_collection = default_collection

    def execute(self, query: str, collection: str = "", k: int = 3) -> str:
        """检索知识库。"""
        col = collection or self._default_collection
        results = self._vs.search(
            collection_name=col,
            query=query,
            k=k,
        )

        if not results:
            return f"知识库 '{col}' 中未找到关于 '{query}' 的内容"

        formatted = [f"知识库 '{col}' 检索结果:"]
        for i, r in enumerate(results, 1):
            score = r.get("score", 0)
            meta = r.get("metadata", {})
            source = meta.get("source", "未知")
            formatted.append(f"{i}. [相似度 {score:.2f}] 来源: {source}\n   {r['content'][:300]}")

        return "\n\n".join(formatted)
