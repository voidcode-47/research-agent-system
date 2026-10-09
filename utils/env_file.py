""".env 文件读写工具（设置页与侧边栏共用）。"""
import re
from pathlib import Path

# 允许 `KEY=v`、`KEY = v`、`export KEY=v` 三种写法
_ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def load_env_lines(path: str | Path = ".env") -> list[str]:
    """读取 .env 文件，返回行列表；文件不存在返回空列表。

    依次尝试 UTF-16（仅在有 BOM 时）/ UTF-8 / GBK，最后兜底替换非法字节——
    绝不能因为编码问题返回空列表，否则 update_env 会把整个文件覆盖没了。
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_bytes()
    if not raw:
        return []

    encodings: list[str] = []
    # 仅在存在 BOM 时才按 UTF-16 解码：UTF-16 对任意偶数长度字节串都能"成功"
    # 解出乱码，会把正常的 ASCII 配置项一并毁掉
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encodings.append("utf-16")
    encodings += ["utf-8-sig", "gbk"]

    for enc in encodings:
        try:
            return raw.decode(enc).splitlines()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").splitlines()


def _line_key(line: str) -> str | None:
    """取一行 env 声明的 key；非键值行（注释/空行）返回 None。"""
    if line.lstrip().startswith("#"):
        return None
    m = _ENV_KEY_RE.match(line)
    return m.group(1) if m else None


def get_env_value(lines: list[str], key: str, default: str = "") -> str:
    """从 env 行中取指定 key 的值。"""
    for line in lines:
        if _line_key(line) == key:
            return line.split("=", 1)[1].strip()
    return default


def update_env(updates: dict[str, str], path: str | Path = ".env") -> Path:
    """更新 .env 中的键值（已有 key 原位更新，新 key 追加），返回文件路径。"""
    p = Path(path)
    lines = load_env_lines(p)
    keys_written = set()

    new_lines = []
    for line in lines:
        key = _line_key(line)
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            keys_written.add(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in keys_written:
            new_lines.append(f"{key}={value}")

    p.write_text("\n".join(new_lines), encoding="utf-8")
    return p
