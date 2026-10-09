"""Token 估算，用于上下文窗口管理。"""
import json
from typing import Optional

# 备用估算系数（tiktoken 不可用时使用）：中文约 1.5 字符=1 token，英文约 4 字符=1 token
_CJK_CHARS_PER_TOKEN = 1.5
_OTHER_CHARS_PER_TOKEN = 4.0

_tiktoken_encoder = None


def _get_encoder():
    """懒加载 tiktoken 编码器（可能不可用）。"""
    global _tiktoken_encoder
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _tiktoken_encoder = False
    return _tiktoken_encoder


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """精确计数 token 数。tiktoken 不可用时回退到估算。"""
    encoder = _get_encoder()
    if encoder:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return estimate_tokens(text)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（按中英文分别取系数，避免中文被低估数倍）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(1, int(cjk / _CJK_CHARS_PER_TOKEN + other / _OTHER_CHARS_PER_TOKEN))


def count_messages_tokens(messages: list[dict]) -> int:
    """计算消息列表的总 token 数。

    必须一并统计 tool_calls/name/tool_call_id：ReAct 的 assistant 消息
    content 为 None，载荷全在 tool_calls 里，漏统计会让预算与压缩守卫失效。
    """
    total = 0
    for msg in messages:
        total += 4  # role + 结构开销
        total += count_tokens(str(msg.get("content") or ""))
        if msg.get("tool_calls"):
            total += count_tokens(json.dumps(msg["tool_calls"], ensure_ascii=False))
        if msg.get("name"):
            total += count_tokens(str(msg["name"]))
        if msg.get("tool_call_id"):
            total += count_tokens(str(msg["tool_call_id"]))
    return total
