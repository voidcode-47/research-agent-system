""".env 文件读写工具（设置页与侧边栏共用）。"""
from pathlib import Path


def load_env_lines(path: str | Path = ".env") -> list[str]:
    """读取 .env 文件，返回行列表；文件不存在返回空列表。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        return p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def get_env_value(lines: list[str], key: str, default: str = "") -> str:
    """从 env 行中取指定 key 的值。"""
    for line in lines:
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def update_env(updates: dict[str, str], path: str | Path = ".env") -> Path:
    """更新 .env 中的键值（已有 key 原位更新，新 key 追加），返回文件路径。"""
    p = Path(path)
    lines = load_env_lines(p)
    keys_written = set()

    new_lines = []
    for line in lines:
        written = False
        for key, value in updates.items():
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                keys_written.add(key)
                written = True
                break
        if not written:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in keys_written:
            new_lines.append(f"{key}={value}")

    p.write_text("\n".join(new_lines), encoding="utf-8")
    return p
