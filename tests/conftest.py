"""pytest 全局 fixtures。"""
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import quota  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_quota_reservations():
    """进程内额度预留不应跨用例残留。"""
    quota._reservations.clear()
    yield
    quota._reservations.clear()
