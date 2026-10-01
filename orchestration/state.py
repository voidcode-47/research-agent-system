"""多智能体共享状态。

这是 LangGraph 节点间传递的数据契约。
"""
from typing import TypedDict, Annotated
from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    """多智能体共享状态。"""
    query: str               # 用户原始问题
    research_data: list[str] # 研究员收集的资料
    analysis: str            # 分析员的结论
    summary: str             # 总结员的最终报告
    messages: Annotated[list, add_messages]  # 消息历史
    current_agent: str       # 当前执行节点
    iteration: int           # 循环计数
    conflicts: list[str]    # 冲突记录
    sources: list[dict]      # 来源列表
    status: str              # 当前状态描述
    error: str               # 错误信息
