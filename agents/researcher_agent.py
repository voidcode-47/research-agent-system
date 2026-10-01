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
            return {
                "research_data": research_data,
                "sources": sources,
                "conflicts": conflicts,
                "status": "research_done",
                "current_agent": "analyst",
            }

        return _node
