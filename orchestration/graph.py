"""LangGraph 多智能体工作流。

构建 supervisor → researcher → analyst → summarizer → quality_check 流程。
quality_check 不通过则回退到 researcher。
"""
from typing import Optional, Iterator
from typing_extensions import TypedDict

from llm.base import BaseLLM
from tools.base import BaseTool
from agents.researcher_agent import ResearcherAgent
from agents.analyst_agent import AnalystAgent
from agents.summarizer_agent import SummarizerAgent
from orchestration.supervisor import Supervisor
from orchestration.state import AgentState
from rag.retriever import Retriever
from utils.logger import get_logger

logger = get_logger(__name__)


def build_research_graph(
    llm: BaseLLM,
    tools: list[BaseTool],
    rag: Optional[Retriever] = None,
    knowledge_collection: str = "default",
    max_iterations: int = 8,
    detailed: bool = False,
):
    """构建多智能体研究工作流。

    Args:
        llm: LLM 实例
        tools: 研究员工具
        rag: RAG 检索器
        knowledge_collection: 知识库集合名
        max_iterations: 研究员 ReAct 最大迭代轮数（控制成本）
        detailed: 总结员是否使用详细版报告提示词（本地 7B 推荐 True）

    Returns:
        编译后的 LangGraph
    """
    from langgraph.graph import StateGraph, END

    # 创建各角色
    supervisor = Supervisor(llm=llm)
    researcher = ResearcherAgent(llm=llm, tools=tools, max_iterations=max_iterations)
    analyst = AnalystAgent(llm=llm, rag_retriever=rag, knowledge_collection=knowledge_collection)
    summarizer = SummarizerAgent(llm=llm, detailed=detailed)

    # 构建图
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("supervisor", supervisor.node_fn())
    graph.add_node("direct_answer", supervisor.direct_answer_fn())
    graph.add_node("researcher", researcher.node_fn())
    graph.add_node("analyst", analyst.node_fn())
    graph.add_node("summarizer", summarizer.node_fn())
    graph.add_node("quality_check", supervisor.quality_check_fn())

    # 设置入口
    graph.set_entry_point("supervisor")

    # supervisor 根据 query 路由
    graph.add_conditional_edges(
        "supervisor",
        supervisor.route_task,
        {
            "research": "researcher",
            "answer_direct": "direct_answer",
        },
    )
    graph.add_edge("direct_answer", END)

    # 研究员 → 分析员 → 总结员
    graph.add_edge("researcher", "analyst")
    graph.add_edge("analyst", "summarizer")
    graph.add_edge("summarizer", "quality_check")

    # 质量检查：通过则结束，不通过回退到研究员
    # 路由读节点已算好的结论，避免同一轮重复调用 LLM 复核
    graph.add_conditional_edges(
        "quality_check",
        supervisor.route_after_quality,
        {
            "pass": END,
            "fail": "researcher",
        },
    )

    return graph.compile()


def run_research(
    graph,
    query: str,
) -> dict:
    """执行研究工作流。

    Args:
        graph: 编译后的 LangGraph
        query: 研究主题

    Returns:
        最终状态（含 summary）
    """
    initial_state: AgentState = {
        "query": query,
        "research_data": [],
        "analysis": "",
        "summary": "",
        "messages": [],
        "current_agent": "supervisor",
        "iteration": 0,
        "conflicts": [],
        "sources": [],
        "status": "started",
    }

    logger.info(f"开始研究: {query}")
    final_state = graph.invoke(initial_state)
    logger.info("研究完成")
    return final_state
