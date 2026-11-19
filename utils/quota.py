"""按日按 key 的 LLM 调用配额（JSON 持久化，保留最近 7 天）。"""
import contextvars
import datetime
import json
import os
import threading
import uuid

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTA_PATH = os.path.join(BASE_DIR, "data", "quota.json")
KEEP_DAYS = 7

_lock = threading.Lock()
# token -> {"key": str, "remaining": int, "original": int}
_reservations: dict[str, dict] = {}
_CURRENT_RESERVATION = contextvars.ContextVar("quota_reservation", default=None)


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
    tmp = (f"{QUOTA_PATH}.{os.getpid()}.{threading.get_ident()}."
           f"{uuid.uuid4().hex[:8]}.tmp")
    try:
        os.makedirs(os.path.dirname(QUOTA_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, QUOTA_PATH)
    except OSError as e:
        logger.warning(f"配额文件写入失败: {e}")
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


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


def _reserved_total(key: str) -> int:
    return sum(
        int(item.get("remaining") or 0)
        for item in _reservations.values()
        if item.get("key") == key
    )


def reserve(amount: int, key: str = "local", unlimited: bool = False) -> str | None:
    """为一次任务预留额度；返回 token，任务结束必须 release。

    预留在内存中，只对当前服务进程有效；服务重启后预留自动消失，
    已发生的调用仍保留在 quota.json 的 used 计数中。
    """
    if unlimited or amount <= 0:
        return None
    today = _today()
    with _lock:
        data = _prune(_load(), today)
        used = _used(data, today, key)
        remaining = max(0, settings.DAILY_QUOTA - used - _reserved_total(key))
        if remaining < amount:
            raise QuotaExceeded(
                f"本次任务需要 {amount} 次模型调用，今日仅剩 {remaining} 次，"
                "配额不足；请减少批量段落数、明天再试，或调整 DAILY_QUOTA。"
            )
        token = uuid.uuid4().hex
        _reservations[token] = {
            "key": key,
            "remaining": int(amount),
            "original": int(amount),
        }
        return token


def release(token: str | None) -> None:
    """释放任务未使用的预留额度。"""
    if not token:
        return
    with _lock:
        _reservations.pop(token, None)


def set_reservation(token: str | None):
    """把预留 token 绑定到当前任务执行上下文。"""
    return _CURRENT_RESERVATION.set(token)


def reset_reservation(context_token) -> None:
    _CURRENT_RESERVATION.reset(context_token)


def check(key: str = "local", unlimited: bool = False) -> int:
    """检查当日剩余额度，超限抛 QuotaExceeded；返回已用次数。"""
    if unlimited:
        return 0
    today = _today()
    with _lock:
        data = _load()
        used = _used(data, today, key)
        reserved = _reserved_total(key)
    if used + reserved >= settings.DAILY_QUOTA:
        raise QuotaExceeded(
            f"今日模型调用配额已用完（{settings.DAILY_QUOTA} 次/天），请明天再试。"
        )
    return used


def preflight(amount: int, key: str = "local", unlimited: bool = False) -> None:
    """检查剩余额度是否覆盖一次多调用任务；不足时抛 QuotaExceeded。

    这是提交前的尽力检查，不是额度预留：检查后其他任务仍可能消耗额度。
    """
    if unlimited or amount <= 0:
        return
    today = _today()
    with _lock:
        used = _used(_load(), today, key)
        reserved = _reserved_total(key)
    remaining = max(0, settings.DAILY_QUOTA - used - reserved)
    if remaining < amount:
        raise QuotaExceeded(
            f"本次任务需要 {amount} 次模型调用，今日仅剩 {remaining} 次，"
            "额度不足；请减少批量段落数、明天再试，或调整 DAILY_QUOTA。"
        )


def consume(key: str = "local", amount: int = 1, unlimited: bool = False) -> int:
    """消耗配额；返回消耗后的当日已用次数。管理员不限额度时不计数。"""
    if unlimited:
        return 0
    today = _today()
    with _lock:
        data = _prune(_load(), today)
        used = _used(data, today, key)
        token = _CURRENT_RESERVATION.get()
        reservation = _reservations.get(token) if token else None
        if reservation and reservation.get("key") == key:
            remaining = int(reservation.get("remaining") or 0)
            if remaining < amount:
                raise QuotaExceeded(
                    f"任务预留额度不足：还需 {amount} 次，仅剩 {remaining} 次；"
                    "请重新发起任务。"
                )
            reservation["remaining"] = remaining - amount
            if reservation["remaining"] <= 0:
                _reservations.pop(token, None)
            data.setdefault(today, {})[key] = used + amount
            _save(data)
            return used + amount
        if used >= settings.DAILY_QUOTA:
            raise QuotaExceeded(
                f"今日模型调用配额已用完（{settings.DAILY_QUOTA} 次/天），请明天再试。"
            )
        data.setdefault(today, {})[key] = used + amount
        _save(data)
        return used + amount


def refund(key: str = "local", amount: int = 1) -> int:
    """退还配额（失败回滚），不会低于 0。"""
    if amount <= 0:
        return 0
    today = _today()
    with _lock:
        data = _load()
        used = _used(data, today, key)
        token = _CURRENT_RESERVATION.get()
        reservation = _reservations.get(token) if token else None
        if reservation and reservation.get("key") == key:
            reservation["remaining"] = min(
                int(reservation.get("original") or 0),
                int(reservation.get("remaining") or 0) + amount,
            )
        data.setdefault(today, {})[key] = max(0, used - amount)
        _save(data)
        return data[today][key]


def usage_snapshot(key: str = "local", unlimited: bool = False) -> dict:
    """配额快照：当日用量、剩余量以及最近 7 天历史。"""
    today = _today()
    data = _load()
    used = _used(data, today, key)
    reserved = _reserved_total(key)
    if unlimited:
        return {
            "date": today,
            "key": key,
            "limit": None,
            "used": used,
            "reserved": reserved,
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
        "reserved": reserved,
        "remaining": max(0, limit - used - reserved),
        "unlimited": False,
        "history": history,
    }
