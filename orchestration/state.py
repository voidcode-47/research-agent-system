"""多智能体共享状态。

这是 LangGraph 节点间传递的数据契约。
"""
from typing import TypedDict, Annotated
from langgraph.graph import add_messages


class AgentState(TypedDict, total=False):
    """多智能体共享状态。"""
    query: str               # 用户原始问题
    research_data: list[str] # 研究员收集的资料
    ref_list: str            # 统一编号的可引用文献清单（总结员唯一引用池）
    analysis: str            # 分析员的结论
    summary: str             # 总结员的最终报告
    messages: Annotated[list, add_messages]  # 消息历史
    current_agent: str       # 当前执行节点
    iteration: int           # 循环计数
    conflicts: list[str]    # 冲突记录
    sources: list[dict]      # 来源列表
    status: str              # 当前状态描述
    error: str               # 错误信息
    research_failed: bool    # 研究员未收集到任何资料
    report_failed: bool      # 总结员未能生成有效报告
    quality_result: str      # 质量检查结论 pass/fail（由质检节点写入，条件边读取）
    quality_degraded: bool   # 质检器本身不可用、按通过处理
