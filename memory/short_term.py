"""短期记忆：滑动窗口 + token 计数 + 超限 LLM 摘要压缩。"""
from typing import Optional

from utils.logger import get_logger
from utils.token_counter import count_tokens, count_messages_tokens

logger = get_logger(__name__)


class ShortTermMemory:
    """短期对话记忆，管理上下文窗口。

    策略：用 tiktoken 精确计数，超限时用 LLM 生成摘要替换旧消息，
    保留系统提示和最近 N 轮。

    消息存储为标准 OpenAI 消息 dict，完整保留 tool_calls / tool_call_id
    字段，避免破坏 Function Calling 多轮对话的配对关系。
    """

    def __init__(self, max_tokens: int = 6000, keep_recent: int = 6):
        """初始化。

        Args:
            max_tokens: 最大 token 数
            keep_recent: 压缩时保留最近几条消息
        """
        self.max_tokens = max_tokens
        self.keep_recent = keep_recent
        self.messages: list[dict] = []
        self._summary: str = ""  # 压缩后的历史摘要

    def add(self, role: str, content: str) -> None:
        """添加一条简单文本消息。"""
        self.messages.append({"role": role, "content": content})

    def add_message(self, message: dict) -> None:
        """添加一条完整消息（可含 tool_calls/tool_call_id 等字段）。"""
        self.messages.append(dict(message))

    def add_system(self, content: str) -> None:
        """添加/替换系统提示。"""
        # 移除旧系统提示（保留"之前的对话摘要"由 get_messages 动态注入）
        self.messages = [
            m for m in self.messages
            if not (m["role"] == "system" and not m.get("_is_summary"))
        ]
        self.messages.insert(0, {"role": "system", "content": content})

    def get_messages(self) -> list[dict]:
        """返回不超过 token 上限的消息列表。

        严格保证 tool_calls 与 tool 消息的配对完整性。
        """
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        non_system = [m for m in self.messages if m["role"] != "system"]

        result = list(system_msgs)

        # 历史摘要
        if self._summary:
            result.append({
                "role": "system",
                "content": f"之前的对话摘要:\n{self._summary}",
            })

        # 从最新开始倒序保留，直到达到 token 上限。
        # assistant(tool_calls) 与其后的 tool 消息必须作为一组整体保留，
        # 否则会触发 OpenAI API 的配对校验错误。
        kept_reversed: list[dict] = []
        token_count = count_messages_tokens(result)
        i = len(non_system) - 1
        while i >= 0:
            msg = non_system[i]

            if msg["role"] == "tool":
                # 收集连续的 tool 消息
                group_reversed: list[dict] = []
                while i >= 0 and non_system[i]["role"] == "tool":
                    group_reversed.append(non_system[i])
                    i -= 1
                # 前一条必须是带 tool_calls 的 assistant，成组保留
                if i >= 0 and non_system[i].get("tool_calls"):
                    group_reversed.append(non_system[i])
                    i -= 1
                group_tokens = sum(
                    count_tokens(str(m.get("content", ""))) + 4
                    + (count_tokens(str(m.get("tool_calls", ""))) + 8 if m.get("tool_calls") else 0)
                    for m in group_reversed
                )
                if token_count + group_tokens > self.max_tokens:
                    break
                kept_reversed.extend(group_reversed)
                token_count += group_tokens
                continue

            if msg.get("tool_calls"):
                # 孤立的 tool_calls 消息（其 tool 结果已不在窗口），
                # 必须丢弃，否则配对不完整
                i -= 1
                continue

            msg_tokens = count_tokens(msg.get("content", "")) + 4
            if token_count + msg_tokens > self.max_tokens:
                break
            kept_reversed.append(msg)
            token_count += msg_tokens
            i -= 1

        result.extend(reversed(kept_reversed))
        return result

    def needs_compression(self) -> bool:
        """是否需要压缩。"""
        return count_messages_tokens(self.get_messages()) >= self.max_tokens

    def compress(self, llm) -> None:
        """用 LLM 压缩旧消息为摘要。"""
        from config.prompts import SUMMARIZE_HISTORY_PROMPT

        non_system = [m for m in self.messages if m["role"] != "system"]
        system_msgs = [m for m in self.messages if m["role"] == "system"]

        if len(non_system) <= self.keep_recent:
            return

        to_compress = non_system[:-self.keep_recent]
        recent = non_system[-self.keep_recent:]

        history_text = "\n".join(
            f"{m['role']}: {str(m.get('content', ''))[:200]}" for m in to_compress
        )

        # recent 中只保留完整的 user/assistant 文本对话，
        # 避免遗留未配对的 tool 消息
        safe_recent = [
            m for m in recent
            if m["role"] in ("user", "assistant") and not m.get("tool_calls")
        ]

        try:
            prompt = SUMMARIZE_HISTORY_PROMPT.format(history=history_text)
            response = llm.chat([{"role": "user", "content": prompt}])
            self._summary = response.content
            self.messages = system_msgs + safe_recent
            logger.info("短期记忆已压缩")
        except Exception as e:
            logger.warning(f"记忆压缩失败: {e}")
            # 回退：直接丢弃最旧的消息
            self.messages = system_msgs + safe_recent

    def clear(self) -> None:
        """清空记忆。"""
        self.messages.clear()
        self._summary = ""

    @property
    def token_count(self) -> int:
        """当前 token 数。"""
        return count_messages_tokens(self.get_messages())

    @property
    def summary(self) -> str:
        return self._summary
