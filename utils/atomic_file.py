"""将完整文件写入同目录的唯一临时文件，再原子替换目标。"""
import os
import tempfile
from pathlib import Path


def atomic_write(path: str | Path, content: str | bytes, *, private: bool = False) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode("utf-8") if isinstance(content, str) else content)
            output.flush()
            os.fsync(output.fileno())
        if private:
            os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return target
