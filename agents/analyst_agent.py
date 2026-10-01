"""分析员 Agent：负责分析和综合信息。

对比多个来源，找出矛盾点，使用 RAG 检索补充上下文。
"""
from typing import Optional

from llm.base import BaseLLM
from rag.retriever import Retriever
from config.prompts import ANALYST_SYSTEM_PROMPT
from utils.logger import get_logger

logger = get_logger(__name__)


class AnalystAgent:
    """分析员 Agent。"""

    def __init__(
        self,
        llm: BaseLLM,
        rag_retriever: Optional[Retriever] = None,
        knowledge_collection: str = "default",
    ):
        self.llm = llm
        self.rag = rag_retriever
        self.knowledge_collection = knowledge_collection

    def node_fn(self):
        """LangGraph 节点函数。"""
        def _node(state: dict) -> dict:
            query = state.get("query", "")
            research_data = state.get("research_data", [])
            logger.info(f"[分析员] 开始分析 {len(research_data)} 条资料")

            # 拼接研究资料
            research_text = "\n\n---\n\n".join(research_data)

            # 用 RAG 检索本地知识库补充
            rag_context = ""
            if self.rag and self.knowledge_collection:
                try:
                    results = self.rag.retrieve(
                        query=query,
                        collection_name=self.knowledge_collection,
                        k=3,
                    )
                    if results:
                        rag_context = "\n\n本地知识库补充:\n" + "\n".join(
                            f"- {r['content'][:200]}" for r in results
                        )
                except Exception as e:
                    logger.warning(f"RAG 检索失败: {e}")

            # 分析
            prompt = f"""请分析以下研究资料，找出关键信息、一致性和矛盾点。

问题: {query}

研究资料:
{research_text}
{rag_context}

要求:
1. 提取关键发现
2. 对比不同来源，找出一致和矛盾
3. 评估信息可靠性
4. 标注置信度
5. 引用资料编号"""

            try:
                response = self.llm.chat([
                    {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ], temperature=0.3)

                analysis = response.content
            except Exception as e:
                logger.error(f"分析失败: {e}")
                analysis = f"分析过程出错: {e}\n\n原始资料:\n{research_text}"

            # 检测矛盾
            conflicts = state.get("conflicts", [])
            if "矛盾" in analysis or "冲突" in analysis:
                conflicts.append("分析中发现信息矛盾")

            logger.info("[分析员] 分析完成")
            return {
                "analysis": analysis,
                "conflicts": conflicts,
                "status": "analysis_done",
                "current_agent": "summarizer",
            }

        return _node
