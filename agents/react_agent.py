"""ReAct Agent 核心循环。

实现 Reason→Act→Execute→Observe 闭环。
这是整个系统第一层的核心。
"""
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional

from llm.base import BaseLLM
from memory.manager import MemoryManager
from tools.base import BaseTool
from utils.logger import get_logger
from utils.safety import SafetyGuard

logger = get_logger(__name__)


class StepType(str, Enum):
    """ReAct 步骤类型。"""
    THOUGHT = "thought"        # 思考
    ACTION = "action"          # 行动（工具调用）
    OBSERVATION = "observation" # 观察（工具结果）
    FINAL = "final"            # 最终答案
    ERROR = "error"            # 错误


@dataclass
class AgentStep:
    """ReAct 循环的一步。"""
    type: StepType
    content: str
    tool_name: str = ""
    tool_args: dict = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args or {},
        }


class ReActAgent:
    """ReAct 循环实现。

    工作流程：
    1. Reason: LLM 思考下一步
    2. Act: 决定调用哪个工具
    3. Execute: 执行工具
    4. Observe: 分析工具结果
    5. 重复直到有最终答案或达到最大迭代
    """

    def __init__(
        self,
        llm: BaseLLM,
        tools: list[BaseTool],
        memory: MemoryManager,
        safety: Optional[SafetyGuard] = None,
        system_prompt: str = "",
    ):
        """初始化 ReAct Agent。

        Args:
            llm: LLM 实例
            tools: 工具列表
            memory: 记忆管理器
            safety: 安全防护，留空则用默认配置
            system_prompt: 系统提示，留空用默认 ReAct 提示
        """
        self.llm = llm
        self.tools = {t.name: t for t in tools}
        self.memory = memory
        self.safety = safety or SafetyGuard()

        from config.prompts import REACT_SYSTEM_PROMPT
        self._default_prompt = system_prompt or REACT_SYSTEM_PROMPT
        self._system_prompt = self._default_prompt

    def _build_system_prompt(self) -> str:
        """构建系统提示，注入工具描述。"""
        tools_desc = "\n".join(
            f"- {t.name}: {t.description}"
            for t in self.tools.values()
        )
        try:
            return self._system_prompt.format(tools_desc=tools_desc)
        except (KeyError, IndexError):
            return self._system_prompt

    def _get_tool_schemas(self) -> list[dict]:
        """获取工具 schema。"""
        return [t.to_openai_schema() for t in self.tools.values()]

    def _execute_tool(self, name: str, arguments: dict) -> str:
        """执行工具调用。"""
        tool = self.tools.get(name)
        if not tool:
            return f"错误: 未知工具 '{name}'。可用工具: {list(self.tools.keys())}"

        # 破坏性操作硬拦截：仅对声明 dangerous=True 的工具生效，
        # 避免误伤搜索/抓取类只读工具的合法查询。
        if getattr(tool, "dangerous", False):
            reason = self.safety.check_destructive(name, arguments or {})
            if reason:
                logger.warning(
                    f"[安全拦截] 工具 {name} 命中破坏性操作: {reason} | 参数={arguments}"
                )
                return (
                    f"【安全拦截】{reason}。\n"
                    "该操作属于高危删除/破坏性命令，已禁止自动执行；"
                    "如确需执行，须由用户明确确认后再运行。"
                )

        try:
            result = tool.execute(**arguments)
            return result
        except Exception as e:
            logger.error(f"工具 {name} 执行失败: {e}")
            return f"工具 {name} 执行出错: {e}"

    def run(self, user_input: str, stream: bool = True) -> Iterator[AgentStep]:
        """执行 ReAct 循环，yield 每一步。

        Args:
            user_input: 用户输入
            stream: 是否流式输出（目前影响最终答案的流式）

        Yields:
            AgentStep: 思考/行动/观察/最终答案
        """
        import json as _json

        self.safety.reset()
        self.memory.add("user", user_input)

        # 设置系统提示
        self.memory.add_system(self._build_system_prompt())

        for _ in range(self.safety.max_iterations):
            self.safety.tick_iteration()

            # 检查 token 预算（按原始记忆计数，截断视图永远达不到阈值）
            messages = self.memory.get_messages(user_input)
            if self.safety.check_budget(self.memory.raw_token_count):
                yield AgentStep(
                    type=StepType.ERROR,
                    content="达到 token 预算上限，强制终止",
                )
                return

            # 进入危险区间，注入强制收敛提示
            if self.safety.is_critical:
                messages.append({
                    "role": "system",
                    "content": "你已经收集了足够信息，请直接给出最终答案，不要再调用工具。",
                })

            # 1. Reason: LLM 思考
            try:
                response = self.llm.chat(
                    messages=messages,
                    tools=self._get_tool_schemas(),
                    temperature=0.3 if self.safety.is_critical else 0.7,
                )
            except Exception as e:
                yield AgentStep(
                    type=StepType.ERROR,
                    content=f"LLM 调用失败: {e}",
                )
                return

            # 记录 token 消耗
            if response.usage:
                self.safety.add_tokens(response.usage.get("total_tokens", 0))

            # 2. 判断是否结束
            if not response.has_tool_calls:
                # 没有工具调用 = 最终答案
                answer = response.content or "（模型未返回内容）"
                self.memory.add("assistant", answer)
                yield AgentStep(
                    type=StepType.FINAL,
                    content=answer,
                )
                return

            # 3. 防死循环检测（先于执行，避免无效调用）
            for tc in response.tool_calls:
                if self.safety.is_looping(tc.name, tc.arguments):
                    yield AgentStep(
                        type=StepType.ERROR,
                        content=f"检测到循环行为（重复调用 {tc.name} 且参数相同），强制终止",
                    )
                    return

            # 4. 写入标准 assistant 消息（一条消息包含全部 tool_calls）。
            #    OpenAI Function Calling 规范要求：
            #    assistant.tool_calls 必须与后续 tool(tool_call_id) 严格配对。
            tool_calls_payload = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ]
            self.memory.add_message({
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": tool_calls_payload,
            })

            # 5. 逐个执行工具并写入配对的 tool 消息
            for tc in response.tool_calls:
                yield AgentStep(
                    type=StepType.ACTION,
                    content=f"调用工具 {tc.name}",
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                )

                # Execute + Observe
                observation = self._execute_tool(tc.name, tc.arguments)
                # 观察结果必须带上工具名与参数：研究员据此收集引用来源，
                # 缺失时报告里的「检索工具记录 / 参考文献」会永远为空。
                yield AgentStep(
                    type=StepType.OBSERVATION,
                    content=observation,
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                )

                self.memory.add_message({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": observation,
                })

            # 压缩记忆（在完整配对写入之后）
            self.memory.compress_if_needed(self.llm)

        # 达到最大迭代
        yield AgentStep(
            type=StepType.ERROR,
            content=f"达到最大迭代次数 {self.safety.max_iterations}，强制终止",
        )

    def run_sync(self, user_input: str) -> tuple[str, list[AgentStep]]:
        """同步执行，返回最终答案和所有步骤。"""
        steps = []
        final_answer = ""
        for step in self.run(user_input, stream=False):
            steps.append(step)
            if step.type in (StepType.FINAL, StepType.ERROR):
                final_answer = step.content
        return final_answer, steps
