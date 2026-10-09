"""研究员 Agent：负责收集和整理信息。

拥有 web_search + web_scraper + knowledge_base 工具。
"""
from typing import Optional

from agents.react_agent import ReActAgent, AgentStep, StepType
from llm.base import BaseLLM
from memory.manager import MemoryManager
from memory.short_term import ShortTermMemory
from tools.base import BaseTool
from utils.safety import SafetyGuard
from config.prompts import RESEARCHER_SYSTEM_PROMPT
from utils.logger import get_logger
from utils.report_refs import build_reference_list

logger = get_logger(__name__)


class ResearcherAgent:
    """研究员 Agent，收集和整理信息。"""

    def __init__(
        self,
        llm: BaseLLM,
        tools: list[BaseTool],
        max_iterations: int = 10,
    ):
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        self._system_prompt = RESEARCHER_SYSTEM_PROMPT

    def node_fn(self):
        """LangGraph 节点函数。"""
        def _node(state: dict) -> dict:
            query = state.get("query", "")
            logger.info(f"[研究员] 开始研究: {query}")

            # 创建临时记忆
            memory = MemoryManager(ShortTermMemory(max_tokens=6000))
            memory.add_system(self._system_prompt)

            # 创建 ReAct Agent
            agent = ReActAgent(
                llm=self.llm,
                tools=self.tools,
                memory=memory,
                safety=SafetyGuard(max_iterations=self.max_iterations),
                system_prompt=self._system_prompt,
            )

            # 执行
            research_data = []
            sources = []
            conflicts = []

            for step in agent.run(query, stream=False):
                if step.type == StepType.OBSERVATION:
                    research_data.append(step.content)
                    # 记录来源
                    if step.tool_name:
                        sources.append({
                            "tool": step.tool_name,
                            "args": step.tool_args,
                        })
                elif step.type == StepType.FINAL:
                    research_data.append(f"研究结论:\n{step.content}")
                elif step.type == StepType.ERROR:
                    conflicts.append(step.content)

            logger.info(f"[研究员] 完成，收集 {len(research_data)} 条信息")

            # 统一编号的可引用文献清单（供总结员作为唯一引用池，避免编号冲突/编造）
            ref_list = build_reference_list(research_data)
            if ref_list:
                logger.info(f"[研究员] 生成可引用文献清单 {len(ref_list.splitlines())} 条")

            # 一条资料都没收集到 = 本轮研究实际失败。
            # 若仍返回 research_done，后续分析员/总结员会基于空资料继续，
            # 最终产出一份无来源的"报告"并被当作有效结果。
            failed = not research_data
            error = ""
            if failed:
                error = "；".join(conflicts) if conflicts else "未收集到任何资料"
                logger.warning(f"[研究员] 研究未产出资料: {error}")

            return {
                "research_data": research_data,
                "ref_list": ref_list,
                "sources": sources,
                "conflicts": conflicts,
                "research_failed": failed,
                "error": error,
                "status": "research_failed" if failed else "research_done",
                "current_agent": "analyst",
            }

        return _node
