"""ChromaDB 向量存储封装。

持久化本地存储，支持增删改查。

设计说明：不使用 ChromaDB 的 embedding_function 注入机制，
而是在 add/query 时直接传入预计算的 embeddings。
原因：
1. 避免不同 chromadb 版本的 EmbeddingFunction 协议差异（1.x 要求
   name/build_from_config/get_config，且持久化后需能从配置重建）；
2. embedding 由我们的 EmbeddingProvider 统一管理（多提供商回退）。
"""
from typing import Optional
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class VectorStore:
    """ChromaDB 封装，embedding 由外部 provider 提供。"""

    def __init__(
        self,
        persist_dir: str = "./data/chroma_db",
        embedding_fn=None,
    ):
        """初始化向量存储。

        Args:
            persist_dir: 持久化目录
            embedding_fn: EmbeddingProvider 实例（需有 embed/embed_one 方法）。
                          必须提供，否则 add/search 将明确报错。
        """
        import chromadb

        self._persist_dir = persist_dir
        Path(persist_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embedding_fn = embedding_fn
        # 注意：不向 chroma 传 embedding_function，全部显式传 embeddings

    def get_or_create_collection(self, name: str):
        """获取或创建集合（不绑定 chroma 侧 embedding 函数）。"""
        return self._client.get_or_create_collection(name=name)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding。"""
        if self._embedding_fn is None:
            raise RuntimeError(
                "VectorStore 未配置 EmbeddingProvider，无法生成向量。"
                "请先在设置页配置云端 API Key 或启动本地 embedding 模型。"
            )
        return self._embedding_fn.embed(texts)

    def _embed_one(self, text: str) -> list[float]:
        """单条 embedding。"""
        if self._embedding_fn is None:
            raise RuntimeError("VectorStore 未配置 EmbeddingProvider")
        if hasattr(self._embedding_fn, "embed_one"):
            return self._embedding_fn.embed_one(text)
        return self._embedding_fn.embed([text])[0]

    def add_documents(
        self,
        collection_name: str,
        documents: list[dict],
        ids: Optional[list[str]] = None,
    ) -> int:
        """添加文档到集合。

        Args:
            collection_name: 集合名
            documents: [{"content": str, "metadata": dict}]
            ids: 可选 ID 列表

        Returns:
            添加的文档数
        """
        if not documents:
            return 0

        collection = self.get_or_create_collection(collection_name)
        texts = [d["content"] for d in documents]
        metadatas = [d.get("metadata", {}) for d in documents]

        if ids is None:
            import uuid
            ids = [str(uuid.uuid4()) for _ in documents]

        # 显式计算并传入 embeddings，绕过 chroma 内置 embedding 函数
        embeddings = self._embed(texts)
        collection.add(
            documents=texts,
            metadatas=metadatas,
            ids=ids,
            embeddings=embeddings,
        )
        logger.info(f"向集合 {collection_name} 添加 {len(documents)} 个文档")
        return len(documents)

    def search(
        self,
        collection_name: str,
        query: str,
        k: int = 5,
    ) -> list[dict]:
        """检索相关文档。

        Returns:
            [{"content": str, "metadata": dict, "score": float}]
        """
        collection = self.get_or_create_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []

        query_embedding = self._embed_one(query)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(k, count),
            include=["documents", "metadatas", "distances"],
        )

        docs = []
        if results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 0
                score = max(0.0, 1.0 - float(dist))  # 距离转相似度
                docs.append({
                    "content": doc,
                    "metadata": meta,
                    "score": score,
                })
        return docs

    def delete_collection(self, name: str) -> None:
        """删除集合。"""
        try:
            self._client.delete_collection(name)
            logger.info(f"删除集合: {name}")
        except Exception as e:
            logger.warning(f"删除集合失败 {name}: {e}")

    def list_collections(self) -> list[str]:
        """列出所有集合名。"""
        return [c.name for c in self._client.list_collections()]

    def get_collection_info(self, name: str) -> dict:
        """获取集合信息。"""
        try:
            collection = self.get_or_create_collection(name)
            return {"name": name, "count": collection.count()}
        except Exception:
            return {"name": name, "count": 0}
