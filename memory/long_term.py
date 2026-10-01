"""长期记忆：ChromaDB 存会话摘要，跨会话检索。"""
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class LongTermMemory:
    """长期记忆，将关键对话/研究摘要存入 ChromaDB。"""

    COLLECTION = "long_term_memory"

    def __init__(self, vector_store):
        """初始化。

        Args:
            vector_store: VectorStore 实例
        """
        self._vs = vector_store

    def remember(self, content: str, metadata: Optional[dict] = None) -> None:
        """记住一条信息。"""
        import uuid
        meta = metadata or {}
        self._vs.add_documents(
            collection_name=self.COLLECTION,
            documents=[{"content": content, "metadata": meta}],
            ids=[str(uuid.uuid4())],
        )
        logger.debug(f"长期记忆保存: {content[:50]}...")

    def recall(self, query: str, k: int = 3) -> list[str]:
        """召回相关记忆。"""
        results = self._vs.search(
            collection_name=self.COLLECTION,
            query=query,
            k=k,
        )
        return [r["content"] for r in results]

    def recall_with_scores(self, query: str, k: int = 3) -> list[dict]:
        """召回相关记忆（带分数）。"""
        return self._vs.search(
            collection_name=self.COLLECTION,
            query=query,
            k=k,
        )

    def clear_all(self) -> None:
        """清空所有长期记忆。"""
        self._vs.delete_collection(self.COLLECTION)
