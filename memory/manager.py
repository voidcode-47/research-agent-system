"""记忆管理器：组合短期+长期记忆，给 Agent 提供单一接口。"""
from typing import Optional

from memory.short_term import ShortTermMemory
from memory.long_term import LongTermMemory
from utils.logger import get_logger

logger = get_logger(__name__)


class MemoryManager:
    """统一记忆管理。

    先检索长期记忆补充上下文，再加入短期记忆。
    """

    def __init__(
        self,
        short_term: ShortTermMemory,
        long_term: Optional[LongTermMemory] = None,
    ):
        self.short = short_term
        self.long = long_term

    @property
    def raw_token_count(self) -> int:
        """短期记忆的原始 token 总量（未经窗口截断）。"""
        return self.short.raw_token_count

    def add(self, role: str, content: str) -> None:
        """添加简单消息到短期记忆。"""
        self.short.add(role, content)

    def add_message(self, message: dict) -> None:
        """添加完整消息（支持 tool_calls/tool_call_id 字段）。"""
        self.short.add_message(message)

    def add_system(self, content: str) -> None:
        """添加/替换系统提示。"""
        self.short.add_system(content)

    def get_messages(self, query: Optional[str] = None) -> list[dict]:
        """获取记忆消息列表。

        Args:
            query: 可选查询，用于从长期记忆召回相关上下文

        Returns:
            消息列表
        """
        messages = self.short.get_messages()

        # 如果有长期记忆且提供了查询，补充上下文
        if self.long and query:
            try:
                memories = self.long.recall(query, k=3)
            except Exception as e:
                # 长期记忆依赖向量库/embedding，不可用时不应中断整轮对话
                logger.warning(f"长期记忆召回失败，跳过: {e}")
                memories = []
            if memories:
                context = "相关历史记忆:\n" + "\n---\n".join(memories)
                # 插入到所有 system 消息之后、第一条对话消息之前
                insert_pos = 0
                while insert_pos < len(messages) and messages[insert_pos]["role"] == "system":
                    insert_pos += 1
                messages.insert(insert_pos, {"role": "system", "content": context})

        return messages

    def remember_important(self, content: str, metadata: Optional[dict] = None) -> None:
        """保存重要信息到长期记忆。"""
        if self.long:
            self.long.remember(content, metadata)

    def compress_if_needed(self, llm) -> None:
        """如果短期记忆超限，压缩。"""
        if self.short.needs_compression():
            self.short.compress(llm)

    def clear(self) -> None:
        """清空短期记忆（保留长期记忆）。"""
        self.short.clear()

    def clear_all(self) -> None:
        """清空所有记忆。"""
        self.short.clear()
        if self.long:
            self.long.clear_all()
