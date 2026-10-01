"""pytest 全局 fixtures。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def mock_llm():
    """模拟 LLM，不消耗 API。"""
    llm = MagicMock()
    llm.ping.return_value = True
    llm.chat.return_value = MagicMock(
        content="这是一个模拟回复",
        tool_calls=[],
        usage={"total_tokens": 100},
    )
    return llm


@pytest.fixture
def mock_chat_response():
    """模拟对话响应。"""
    from llm.base import ChatResponse
    return ChatResponse(content="测试回复", usage={"total_tokens": 50})


@pytest.fixture
def temp_vector_store(tmp_path):
    """临时向量存储（不初始化 embedding）。"""
    with patch("tools.vector_store.VectorStore.__init__", return_value=None):
        from tools.vector_store import VectorStore
        vs = VectorStore()
        vs._client = MagicMock()
        return vs
