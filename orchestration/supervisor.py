"""编排者：任务分发 + 冲突解决 + 进度跟踪。"""
import json
import re
from typing import Optional

from llm.base import BaseLLM
from utils.logger import get_logger

logger = get_logger(__name__)

# 寒暄类短句：中文按前缀匹配，拉丁文按整词匹配
_GREETING_PREFIXES = ("你好", "您好", "谢谢", "再见", "嗨", "在吗")
_GREETING_WORDS = ("hi", "hello")


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
        stripped = query.strip().lower()
        if len(stripped) < 12:
            if any(stripped.startswith(g) for g in _GREETING_PREFIXES):
                return "answer_direct"
            # 拉丁问候语必须整词匹配，否则 "higgs boson" 会被 "hi" 误判
            if any(re.match(rf"{g}\b", stripped) for g in _GREETING_WORDS):
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

    def _evaluate_quality(self, state: dict) -> tuple[str, bool]:
        """质量检查核心逻辑。

        Returns:
            (verdict, degraded)：verdict 为 "pass"/"fail"；
            degraded=True 表示检查器本身不可用、只能按通过放行。
        """
        iteration = state.get("iteration", 0)
        if iteration >= 2:
            logger.info("达到最大重试，强制通过")
            return "pass", False

        # 研究/报告阶段已明确失败：重试只会再失败一次，直接结束流程
        # （空 summary 会被上层判为无效结果）
        if state.get("research_failed") or state.get("report_failed"):
            logger.warning("上游研究或报告生成失败，结束流程")
            return "pass", False

        summary = state.get("summary", "")
        if not summary or len(summary) < 50:
            return "fail", False

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

                match = re.search(r'\{.*\}', response.content, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                    if not result.get("pass", True):
                        logger.info(f"质量检查未通过: {result.get('issues', [])}")
                        return "fail", False
            except Exception as e:
                logger.warning(f"质量检查失败，按通过处理: {e}")
                return "pass", True

        return "pass", False

    def check_quality(self, state: dict) -> str:
        """质量检查：是否通过。

        Returns:
            "pass" 通过 / "fail" 不通过，回退到研究员
        """
        return self._evaluate_quality(state)[0]

    def route_after_quality(self, state: dict) -> str:
        """条件边路由：读取节点已算好的结论，避免重复调用 LLM 复核。"""
        result = state.get("quality_result")
        if result in ("pass", "fail"):
            return result
        # 兜底：节点未写入结论时（如直接调用路由）现算一次
        return self.check_quality(state)

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
            # 先递增再校验：check_quality 读的是本轮迭代号，
            # 传旧 state 会让「最多重试 2 次」实际多跑一轮
            next_iteration = state.get("iteration", 0) + 1
            result, degraded = self._evaluate_quality({**state, "iteration": next_iteration})
            logger.info(f"[质量检查] 第 {next_iteration} 轮: {result}"
                        + ("（检查器不可用，按通过处理）" if degraded else ""))
            return {
                "iteration": next_iteration,
                "quality_result": result,
                "quality_degraded": degraded,
                "status": f"quality_{result}",
                "current_agent": "researcher" if result == "fail" else "end",
            }
        return _node
