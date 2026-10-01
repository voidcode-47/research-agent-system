"""RAG 检索器：向量检索 + 可选 LLM 重排 + answer_with_context 完整链路。"""
from typing import Optional

from llm.base import BaseLLM
from tools.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)


class Retriever:
    """RAG 检索器。

    完整链路：检索 → 重排 → 拼接上下文 → 生成答案
    """

    def __init__(
        self,
        vector_store: VectorStore,
        llm: Optional[BaseLLM] = None,
        top_k: int = 5,
        score_threshold: float = 0.3,
    ):
        """初始化。

        Args:
            vector_store: 向量存储
            llm: 可选 LLM，用于重排和答案生成
            top_k: 检索数量
            score_threshold: 相似度阈值
        """
        self.vs = vector_store
        self.llm = llm
        self.top_k = top_k
        self.score_threshold = score_threshold

    def retrieve(
        self,
        query: str,
        collection_name: str,
        k: Optional[int] = None,
    ) -> list[dict]:
        """检索相关文档。

        Returns:
            [{"content": str, "metadata": dict, "score": float}]
        """
        k = k or self.top_k
        results = self.vs.search(
            collection_name=collection_name,
            query=query,
            k=k * 2,  # 多检索一些用于重排
        )

        # 过滤低分
        filtered = [r for r in results if r["score"] >= self.score_threshold]

        # 取 top_k
        return filtered[:k]

    def rerank_with_llm(self, query: str, docs: list[dict]) -> list[dict]:
        """用 LLM 重排文档（如果可用）。"""
        if not self.llm or len(docs) <= 1:
            return docs

        try:
            docs_text = "\n\n".join(
                f"[{i}] {d['content'][:200]}"
                for i, d in enumerate(docs)
            )
            prompt = f"""请根据查询相关性对以下文档片段排序，返回排序后的编号列表（如 [2,0,1]）。

查询: {query}

文档:
{docs_text}

只返回 JSON 数组，不要其他内容。"""

            response = self.llm.chat([
                {"role": "system", "content": "你是一个文档相关性排序助手。"},
                {"role": "user", "content": prompt},
            ], temperature=0.0)

            import json
            import re
            # 提取 JSON 数组
            match = re.search(r'\[([0-9,\s]+)\]', response.content)
            if match:
                order = [int(x.strip()) for x in match.group(1).split(",")]
                reranked = [docs[i] for i in order if i < len(docs)]
                # 补充遗漏的
                for i, d in enumerate(docs):
                    if d not in reranked:
                        reranked.append(d)
                return reranked
        except Exception as e:
            logger.debug(f"重排失败，使用原始顺序: {e}")

        return docs

    def answer_with_context(
        self,
        query: str,
        collection_name: str,
        k: Optional[int] = None,
    ) -> dict:
        """RAG 完整链路：检索+拼接+生成。

        Returns:
            {"answer": str, "sources": list[dict]}
        """
        # 1. 检索
        docs = self.retrieve(query, collection_name, k)

        if not docs:
            return {
                "answer": "未找到相关资料。请先上传文档到知识库。",
                "sources": [],
            }

        # 2. 重排
        if self.llm:
            docs = self.rerank_with_llm(query, docs)

        # 3. 拼接上下文
        context_parts = []
        sources = []
        for i, d in enumerate(docs):
            context_parts.append(f"[{i+1}] {d['content']}")
            sources.append({
                "content": d["content"][:200] + "...",
                "metadata": d["metadata"],
                "score": d["score"],
            })

        context = "\n\n".join(context_parts)

        # 4. 生成答案
        if not self.llm:
            return {"answer": context, "sources": sources}

        prompt = f"""请根据以下资料回答问题。回答时标注引用编号如 [1]、[2]。

问题: {query}

资料:
{context}

要求:
- 只基于资料回答，不编造
- 如果资料不足，说明缺失
- 引用来源编号"""

        try:
            response = self.llm.chat([
                {"role": "system", "content": "你是一个基于资料的研究助手。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)

            return {"answer": response.content, "sources": sources}
        except Exception as e:
            logger.error(f"答案生成失败: {e}")
            return {"answer": context, "sources": sources}
