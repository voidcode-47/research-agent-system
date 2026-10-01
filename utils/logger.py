"""统一日志，控制台彩色输出。"""
import logging
import sys
from typing import Optional


def get_logger(name: str = "research_assistant", level: Optional[str] = None) -> logging.Logger:
    """获取统一配置的 logger。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_str = level or "INFO"
    logger.setLevel(getattr(logging, level_str.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = get_logger()
