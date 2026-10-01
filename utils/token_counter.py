"""Token 估算，用于上下文窗口管理。"""
from typing import Optional

# 备用估算系数（中文约 1.5 字符=1 token，英文约 4 字符=1 token）
_CHAR_RATIO = 2.5

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
    """粗略估算 token 数。"""
    if not text:
        return 0
    return max(1, int(len(text) / _CHAR_RATIO))


def count_messages_tokens(messages: list[dict]) -> int:
    """计算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        total += 4  # role + 结构开销
        total += count_tokens(msg.get("content", ""))
    return total
