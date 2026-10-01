"""批量 Embedding 生成器。"""
from typing import Optional

from llm.embedding import EmbeddingProvider
from utils.logger import get_logger

logger = get_logger(__name__)


class BatchEmbedder:
    """批量 Embedding 生成，用于文档入库。"""

    def __init__(self, provider: EmbeddingProvider):
        self.provider = provider

    def embed_texts(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        """批量生成 embedding。"""
        if not texts:
            return []

        if show_progress:
            logger.info(f"开始 embedding {len(texts)} 段文本...")

        results = self.provider.embed(texts)

        if show_progress:
            logger.info(f"Embedding 完成，维度 {len(results[0]) if results else 0}")
        return results
