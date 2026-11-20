"""本地管理员认证：单账号 + 内存会话 + PBKDF2 密码哈希。

首次启动若未配置 ADMIN_PASSWORD，会生成随机密码并写入
data/admin_credentials.txt（仅本机可见，建议登录后妥善保存并删除该文件）。
"""
import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from contextvars import ContextVar

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
ADMIN_PATH = os.path.join(DATA_DIR, "admin.json")
CREDENTIALS_PATH = os.path.join(DATA_DIR, "admin_credentials.txt")

COOKIE_NAME = "admin_session"
# 7 天有效：本地单机工具，避免频繁重新登录；会话落盘后重启服务也不会掉线
SESSION_TTL = 7 * 24 * 3600
MAX_FAILS = 5
FAIL_WINDOW = 300
_ITERATIONS = 210_000
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

_sessions: dict[str, dict] = {}
_sessions_loaded = False
_failures: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()
_admin_ctx: ContextVar[bool] = ContextVar("admin_ctx", default=False)


def _sessions_path() -> str:
    # 跟随 ADMIN_PATH 所在目录，测试替换 ADMIN_PATH 时自动隔离
    return os.path.join(os.path.dirname(ADMIN_PATH), "admin_sessions.json")


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_sessions_locked(now: float) -> None:
    global _sessions_loaded
    if _sessions_loaded:
        return
    _sessions_loaded = True
    try:
        with open(_sessions_path(), encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("sessions") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        records = None
    if not isinstance(records, dict):
        return
    _sessions.clear()
    for key, rec in records.items():
        if isinstance(rec, dict) and rec.get("username") and rec.get("expires", 0) > now:
            _sessions[key] = {"username": str(rec["username"]), "expires": float(rec["expires"])}


def _save_sessions_locked() -> None:
    path = _sessions_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_private(path, json.dumps({"sessions": _sessions}, ensure_ascii=False, indent=2))
    except OSError as e:
        logger.warning(f"管理员会话写入失败: {e}")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_admin() -> bool:
    return bool(_admin_ctx.get())


def set_admin(flag: bool):
    return _admin_ctx.set(bool(flag))


def reset_admin(token) -> None:
    try:
        _admin_ctx.reset(token)
    except (ValueError, RuntimeError):
        pass


def reset_state() -> None:
    """测试辅助：清空会话、失败计数与上下文。"""
    global _sessions_loaded
    with _lock:
        _sessions.clear()
        _sessions_loaded = False
        _failures.clear()
    _admin_ctx.set(False)


def _load_record() -> dict:
    try:
        with open(ADMIN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_private(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash_password(password: str, salt_hex: str = "",
                   iterations: int = _ITERATIONS) -> tuple[str, str]:
    salt = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
    )
    return salt, digest.hex()


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = str(record.get("salt") or "")
        expected = str(record.get("hash") or "")
        iterations = int(record.get("iterations") or _ITERATIONS)
        if not salt or not expected:
            return False
        _, actual = _hash_password(password, salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _save_record(username: str, password: str) -> None:
    salt, digest = _hash_password(password)
    record = {
        "username": username,
        "salt": salt,
        "hash": digest,
        "iterations": _ITERATIONS,
        "updated_at": _now(),
    }
    _write_private(ADMIN_PATH, json.dumps(record, ensure_ascii=False, indent=2))


def _generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _write_credentials(username: str, password: str) -> str:
    text = (
        "论文工作台 · 管理员账号\n"
        "========================\n"
        f"用户名：{username}\n"
        f"密码：{password}\n"
        f"生成时间：{_now()}\n\n"
        "管理员权限：修改模型配置、每日调用不限额度。\n"
        "请登录后立即妥善保存本文件，并删除它或修改 .env 中的 ADMIN_PASSWORD。\n"
    )
    try:
        _write_private(CREDENTIALS_PATH, text)
        return CREDENTIALS_PATH
    except OSError as e:
        logger.warning(f"管理员初始凭据写入失败: {e}")
        return ""


def ensure_admin() -> dict:
    """确保管理员账号存在；环境变量只在首次创建账号时作为初始密码。"""
    username = (settings.ADMIN_USERNAME or "admin").strip() or "admin"
    env_password = (settings.ADMIN_PASSWORD or "").strip()
    record = _load_record()
    if record.get("username") and record.get("hash"):
        if record.get("username") != username:
            record["username"] = username
            record["updated_at"] = _now()
            _write_private(ADMIN_PATH, json.dumps(record, ensure_ascii=False, indent=2))
        return {"username": username, "created": False, "credentials_path": ""}
    if env_password:
        _save_record(username, env_password)
        return {"username": username, "created": True, "credentials_path": ""}
    password = _generate_password()
    _save_record(username, password)
    path = _write_credentials(username, password)
    logger.warning(f"已生成管理员账号 {username}，初始密码见 {path or '启动日志'}")
    return {"username": username, "created": True, "credentials_path": path}


def change_password(current_password: str, new_password: str) -> bool:
    """验证当前凭据并原子替换已保存的密码哈希。"""
    ensure_admin()
    record = _load_record()
    if not verify_password(current_password or "", record):
        return False
    _save_record(str(record.get("username") or settings.ADMIN_USERNAME or "admin"), new_password)
    return True


def _prune_sessions(now: float) -> None:
    expired = [t for t, s in _sessions.items() if s.get("expires", 0) <= now]
    for token in expired:
        _sessions.pop(token, None)


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _load_sessions_locked(now)
        _prune_sessions(now)
        _sessions[_token_key(token)] = {"username": username, "expires": now + SESSION_TTL}
        _save_sessions_locked()
    return token


def session_username(token: str) -> str:
    if not token:
        return ""
    now = time.time()
    with _lock:
        _load_sessions_locked(now)
        _prune_sessions(now)
        data = _sessions.get(_token_key(token))
        return str(data.get("username") or "") if data else ""


def destroy_session(token: str) -> None:
    if not token:
        return
    now = time.time()
    with _lock:
        _load_sessions_locked(now)
        if _sessions.pop(_token_key(token), None) is not None:
            _save_sessions_locked()


def revoke_sessions_except(token: str) -> None:
    """密码更改后使其他浏览器会话失效，保留当前登录会话。"""
    keep = _token_key(token) if token else ""
    now = time.time()
    with _lock:
        _load_sessions_locked(now)
        retained = {key: value for key, value in _sessions.items() if key == keep}
        if retained != _sessions:
            _sessions.clear()
            _sessions.update(retained)
            _save_sessions_locked()


def _throttled(key: str, now: float) -> bool:
    fails, first = _failures.get(key, (0, 0.0))
    if fails >= MAX_FAILS and now - first < FAIL_WINDOW:
        return True
    if fails and now - first >= FAIL_WINDOW:
        _failures.pop(key, None)
    return False


def _record_failure(key: str, now: float) -> None:
    fails, first = _failures.get(key, (0, now))
    if now - first >= FAIL_WINDOW:
        fails, first = 0, now
    _failures[key] = (fails + 1, first)


def login(username: str, password: str) -> tuple[str, str]:
    """返回 (session_token, error)；失败时 token 为空。"""
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return "", "请输入用户名和密码"
    now = time.time()
    key = username.lower()
    with _lock:
        if _throttled(key, now):
            return "", "登录失败次数过多，请 5 分钟后再试"

    ensure_admin()
    record = _load_record()
    ok = (
        bool(record)
        and hmac.compare_digest(username, str(record.get("username") or ""))
        and verify_password(password, record)
    )
    if not ok:
        with _lock:
            _record_failure(key, now)
        return "", "用户名或密码错误"

    with _lock:
        _failures.pop(key, None)
    return create_session(username), ""
