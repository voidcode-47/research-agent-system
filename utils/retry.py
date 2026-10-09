"""重试与退避装饰器。"""
import time
import functools
from typing import Callable, Type, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)


def retry(
    max_attempts: int = 3,
    backoff: float = 1.5,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """指数退避重试装饰器。

    Args:
        max_attempts: 最大尝试次数
        backoff: 退避基数（每次等待 backoff^attempt 秒）
        exceptions: 触发重试的异常类型
    """
    # max_attempts <= 0 时循环一次都不执行，末尾会 raise None 变成
    # "TypeError: exceptions must derive from BaseException"，这里直接兜底为 1 次
    max_attempts = max(1, int(max_attempts))

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    wait = backoff ** (attempt + 1)
                    logger.warning(
                        f"{func.__name__} 第 {attempt+1}/{max_attempts} 次失败: {e}，"
                        f"等待 {wait:.1f}s 重试"
                    )
                    if attempt < max_attempts - 1:
                        time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator
