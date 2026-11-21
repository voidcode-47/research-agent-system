"""Serialize configuration read/modify/write transactions within the service."""
import functools
import threading

LOCK = threading.RLock()


def transaction(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with LOCK:
            return function(*args, **kwargs)
    return wrapped
