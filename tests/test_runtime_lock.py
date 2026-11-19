"""跨进程实例锁：第二个服务进程不能同时使用同一数据目录。"""
import subprocess
import sys
from pathlib import Path

from utils import runtime_lock

ROOT = Path(__file__).resolve().parents[1]
_CHILD = """
import sys
from utils import runtime_lock

ok = runtime_lock.acquire(sys.argv[1])
print("locked" if ok else "busy")
runtime_lock.release()
"""


def _child_acquire(path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_second_process_is_rejected_until_release(tmp_path):
    path = tmp_path / "server.lock"
    assert runtime_lock.acquire(path) is True
    try:
        assert _child_acquire(path) == "busy"
    finally:
        runtime_lock.release()

    assert _child_acquire(path) == "locked"


def test_same_process_acquire_is_reentrant(tmp_path):
    path = tmp_path / "server.lock"
    assert runtime_lock.acquire(path) is True
    assert runtime_lock.acquire(path) is True
    try:
        runtime_lock.release()
        assert _child_acquire(path) == "busy"
    finally:
        runtime_lock.release()

    assert _child_acquire(path) == "locked"
