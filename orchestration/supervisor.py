"""编排者：任务分发 + 冲突解决 + 进度跟踪。"""
from typing import Optional

from llm.base import BaseLLM
from utils.logger import get_logger

logger = get_logger(__name__)


class Supervisor:
    """编排者，决定流程走向。"""

    def __init__(self, llm: Optional[BaseLLM] = None):
        self.llm = llm

    def route_task(self, state: dict) -> str:
        """判断问题是否需要完整研究流程。

        Returns:
            "research" 需要研究流程 / "answer_direct" 直接问答
        """
        query = state.get("query", "")
        if not query:
            return "answer_direct"

        # 仅寒暄类短句直接回答；知识性问题即使短也走研究流程
        greetings = ("你好", "您好", "谢谢", "再见", "hi", "hello", "嗨", "在吗")
        stripped = query.strip().lower()
        if len(stripped) < 12 and any(stripped.startswith(g) for g in greetings):
            return "answer_direct"

        return "research"

    def direct_answer_fn(self):
        """直接问答节点（不走研究流程）。"""
        def _node(state: dict) -> dict:
            query = state.get("query", "")
            logger.info("[编排者] 直接回答")
            if not self.llm:
                return {"summary": query, "status": "direct_done"}
            try:
                resp = self.llm.chat([
                    {"role": "system", "content": "你是研究助手，请简洁友好地回答。"},
                    {"role": "user", "content": query},
                ], temperature=0.7)
                return {
                    "summary": resp.content,
                    "status": "direct_done",
                    "current_agent": "end",
                }
            except Exception as e:
                return {"summary": f"回答失败: {e}", "status": "error"}
        return _node

    def check_quality(self, state: dict) -> str:
        """质量检查：是否通过。

        Returns:
            "pass" 通过 / "fail" 不通过，回退到研究员
        """
        iteration = state.get("iteration", 0)
        if iteration >= 2:
            logger.info("达到最大重试，强制通过")
            return "pass"

        summary = state.get("summary", "")
        if not summary or len(summary) < 50:
            return "fail"

        # 如果有 LLM，做深度检查
        if self.llm:
            try:
                from config.prompts import QUALITY_CHECK_PROMPT
                sources = state.get("research_data", [])
                prompt = QUALITY_CHECK_PROMPT.format(
                    report=summary,
                    sources="\n".join(sources[:3]) if sources else "无",
                )
                response = self.llm.chat([{
                    "role": "user",
                    "content": prompt,
                }], temperature=0.0)

                import json
                import re
                match = re.search(r'\{.*\}', response.content, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    if not result.get("pass", True):
                        logger.info(f"质量检查未通过: {result.get('issues', [])}")
                        return "fail"
            except Exception as e:
                logger.warning(f"质量检查失败，默认通过: {e}")

        return "pass"

    def node_fn(self):
        """LangGraph 节点函数。"""
        def _node(state: dict) -> dict:
            query = state.get("query", "")
            route = self.route_task(state)
            logger.info(f"[编排者] 路由决策: {route}")
            return {
                "status": f"routed_{route}",
                "current_agent": route,
            }
        return _node

    def quality_check_fn(self):
        """质量检查节点。"""
        def _node(state: dict) -> dict:
            iteration = state.get("iteration", 0) + 1
            result = self.check_quality(state)
            logger.info(f"[质量检查] 第 {iteration} 轮: {result}")
            return {
                "iteration": iteration,
                "status": f"quality_{result}",
                "current_agent": "researcher" if result == "fail" else "end",
            }
        return _node
