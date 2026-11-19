"""服务实例文件锁：同一论文数据目录只允许一个服务进程写入。"""
import os
import threading

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

_STATE_LOCK = threading.Lock()
_handle = None
_depth = 0


class InstanceLockError(RuntimeError):
    """当前平台无法提供文件锁。"""


def acquire(path: str) -> bool:
    """非阻塞获取独占锁；已被其他进程占用时返回 False。

    同进程重复获取只增加引用计数，便于生命周期钩子重复进入。
    """
    global _handle, _depth
    with _STATE_LOCK:
        if _handle is not None:
            _depth += 1
            return True
        target = os.path.abspath(os.fspath(path))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        handle = open(target, "a+b")
        try:
            if msvcrt is not None:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise InstanceLockError("当前平台不支持服务实例文件锁")
        except OSError:
            handle.close()
            return False
        except Exception:
            handle.close()
            raise
        try:
            handle.seek(0)
            handle.write(f"pid={os.getpid()}\n".encode("ascii"))
            handle.flush()
        except OSError:
            pass
        _handle = handle
        _depth = 1
        return True


def release() -> None:
    """释放引用；最后一次释放时解除文件锁并关闭句柄。"""
    global _handle, _depth
    with _STATE_LOCK:
        if _handle is None:
            return
        _depth -= 1
        if _depth > 0:
            return
        handle, _handle, _depth = _handle, None, 0
        try:
            if msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()
