"""按日按 key 的 LLM 调用配额（JSON 持久化，保留最近 7 天）。"""
import datetime
import json
import os
import threading

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTA_PATH = os.path.join(BASE_DIR, "data", "quota.json")
KEEP_DAYS = 7

_lock = threading.Lock()


class QuotaExceeded(Exception):
    """当日配额用尽。"""


def _today() -> str:
    """当前日期字符串（独立函数便于测试 monkeypatch）。"""
    return datetime.date.today().isoformat()


def _load() -> dict:
    try:
        with open(QUOTA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(QUOTA_PATH), exist_ok=True)
        tmp = QUOTA_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, QUOTA_PATH)
    except OSError as e:
        logger.warning(f"配额文件写入失败: {e}")


def _prune(data: dict, today: str) -> dict:
    """只保留最近 7 天（含今天）的记录。"""
    try:
        cutoff = datetime.date.fromisoformat(today) - datetime.timedelta(days=KEEP_DAYS - 1)
    except ValueError:
        return data
    kept = {}
    for day, keys in data.items():
        try:
            d = datetime.date.fromisoformat(day)
        except ValueError:
            continue
        if d >= cutoff and isinstance(keys, dict):
            kept[day] = keys
    return kept


def _used(data: dict, day: str, key: str) -> int:
    try:
        return int((data.get(day) or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


def check(key: str = "local", unlimited: bool = False) -> int:
    """检查当日剩余额度，超限抛 QuotaExceeded；返回已用次数。"""
    if unlimited:
        return 0
    today = _today()
    with _lock:
        data = _load()
        used = _used(data, today, key)
    if used >= settings.DAILY_QUOTA:
        raise QuotaExceeded(
            f"今日模型调用配额已用完（{settings.DAILY_QUOTA} 次/天），请明天再试。"
        )
    return used


def consume(key: str = "local", amount: int = 1, unlimited: bool = False) -> int:
    """消耗配额；返回消耗后的当日已用次数。管理员不限额度时不计数。"""
    if unlimited:
        return 0
    today = _today()
    with _lock:
        data = _prune(_load(), today)
        used = _used(data, today, key)
        if used >= settings.DAILY_QUOTA:
            raise QuotaExceeded(
                f"今日模型调用配额已用完（{settings.DAILY_QUOTA} 次/天），请明天再试。"
            )
        data.setdefault(today, {})[key] = used + amount
        _save(data)
        return used + amount


def refund(key: str = "local", amount: int = 1) -> int:
    """退还配额（失败回滚），不会低于 0。"""
    today = _today()
    with _lock:
        data = _load()
        used = _used(data, today, key)
        data.setdefault(today, {})[key] = max(0, used - amount)
        _save(data)
        return data[today][key]


def usage_snapshot(key: str = "local", unlimited: bool = False) -> dict:
    """配额快照：当日用量、剩余量以及最近 7 天历史。"""
    today = _today()
    data = _load()
    used = _used(data, today, key)
    if unlimited:
        return {
            "date": today,
            "key": key,
            "limit": None,
            "used": used,
            "remaining": None,
            "unlimited": True,
            "history": [],
        }
    limit = settings.DAILY_QUOTA
    history = [
        {"date": day, "used": _used(data, day, key)}
        for day in sorted(_prune(data, today).keys())
    ]
    return {
        "date": today,
        "key": key,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "unlimited": False,
        "history": history,
    }
