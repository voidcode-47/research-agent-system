# -*- coding: utf-8 -*-
"""server.py（FastAPI Web 服务）API 层测试。"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server
from fastapi.testclient import TestClient

client = TestClient(server.app)


class _FakeStreamLLM:
    """假 LLM：支持 stream_chat / chat，验证 API 路由逻辑。"""

    def __init__(self):
        self.chunks = ["你好", "，", "世界"]
        self.last_messages = None
        self.last_kwargs = None

    def stream_chat(self, messages, **kwargs):
        self.last_messages = messages
        self.last_kwargs = kwargs
        return iter(self.chunks)

    def chat(self, messages, **kwargs):
        from llm.base import ChatResponse
        self.last_messages = messages
        return ChatResponse(content="计划\n关键词：a、b", usage={})


@pytest.fixture(autouse=True)
def _clean_tasks():
    server.RESEARCH_TASKS.clear()
    yield
    server.RESEARCH_TASKS.clear()


# ---------- 基础 ----------
def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_providers():
    r = client.get("/api/providers")
    assert r.status_code == 200
    data = r.json()
    assert "deepseek" in data["providers"]
    assert "custom" in data["providers"]
    assert "labels" in data


def test_models_known_provider():
    r = client.get("/api/models?provider=deepseek")
    assert r.status_code == 200
    assert "deepseek-chat" in r.json()["models"]


def test_models_unknown_provider():
    r = client.get("/api/models?provider=no_such_provider")
    assert r.status_code == 200
    assert r.json()["models"] == []


# ---------- 报告历史 ----------
def test_reports_list(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    (tmp_path / "ok.md").write_text("# 研究报告：正常\n---\n内容 [1]。", encoding="utf-8")
    (tmp_path / "fail.md").write_text(
        "# 研究报告：失败\n---\n⚠️ API 限流（请求过于频繁）：429", encoding="utf-8")
    r = client.get("/api/reports")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["reports"]]
    assert "ok.md" in names
    assert "fail.md" not in names  # 失败报告不显示


def test_report_download(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    (tmp_path / "ok.md").write_text("# 标题\n---\n正文", encoding="utf-8")
    r = client.get("/api/reports/ok.md")
    assert r.status_code == 200
    assert "# 标题" in r.text


def test_report_download_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    r = client.get("/api/reports/..%2F..%2FREADME.md")
    assert r.status_code in (400, 404)


def test_report_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    f = tmp_path / "del.md"
    f.write_text("# x", encoding="utf-8")
    r = client.delete("/api/reports/del.md")
    assert r.status_code == 200
    assert not f.exists()
    r2 = client.delete("/api/reports/del.md")
    assert r2.status_code == 404


# ---------- 聊天 SSE ----------
def test_chat_direct_stream():
    fake = _FakeStreamLLM()
    with patch("server.LLMFactory.create", return_value=fake):
        r = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "你好"}],
            "mode": "chat",
        })
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "你好" in r.text
    assert '"done"' in r.text
    # 纯聊天模式不应带工具
    assert fake.last_messages[0]["role"] == "system"


def test_chat_empty_messages():
    r = client.post("/api/chat", json={"messages": []})
    assert r.status_code == 200
    assert "消息为空" in r.text


def test_chat_llm_error():
    class _Err:
        def stream_chat(self, messages, **kwargs):
            raise RuntimeError("boom")
    with patch("server.LLMFactory.create", return_value=_Err()):
        r = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "mode": "chat",
        })
    assert "对话失败" in r.text


def test_chat_deep_mode_uses_tools():
    """深度模式：应走 ReAct 路径（含工具调用事件或最终答案）。"""
    from agents.react_agent import AgentStep, StepType
    fake = _FakeStreamLLM()

    class _FakeReAct:
        def __init__(self, *a, **kw):
            pass

        def run(self, user_input):
            yield AgentStep(type=StepType.ACTION, content="调用工具 web_search_cn",
                            tool_name="web_search_cn", tool_args={"query": "x"})
            yield AgentStep(type=StepType.FINAL, content="最终答案")

    with patch("server.LLMFactory.create", return_value=fake), \
         patch("agents.react_agent.ReActAgent", _FakeReAct):
        r = client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "研究一下深度学习进展"}],
            "mode": "deep",
            "tools": ["web_search_cn"],
        })
    assert r.status_code == 200
    assert "tool" in r.text
    assert "最终答案" in r.text


# ---------- 研究任务 ----------
def test_research_requires_topic():
    r = client.post("/api/research", json={"topic": "   "})
    assert r.status_code == 400


def test_research_starts_task():
    captured = {}

    def _fake_worker(task_id, req):
        captured["task_id"] = task_id
        server.RESEARCH_TASKS[task_id].update(status="done", report="# 报告", report_file="x.md")

    with patch("server._run_research_worker", _fake_worker):
        r = client.post("/api/research", json={"topic": "测试主题"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    assert task_id == captured["task_id"]
    st = client.get(f"/api/research/{task_id}")
    assert st.json()["status"] == "done"


def test_research_unknown_task():
    r = client.get("/api/research/nonexistent")
    assert r.status_code == 404


def test_research_failed_report_not_saved():
    """失败报告（限流文案）应标记 failed 且不带 report。"""
    captured = {}

    def _fake_worker(task_id, req):
        server.RESEARCH_TASKS[task_id].update(
            status="failed",
            error="⚠️ API 限流（请求过于频繁）：429",
        )

    with patch("server._run_research_worker", _fake_worker):
        r = client.post("/api/research", json={"topic": "t"})
    st = client.get(f"/api/research/{r.json()['task_id']}").json()
    assert st["status"] == "failed"
    assert st["report"] is None


# ---------- 知识库 ----------
def test_kb_unavailable_without_embedding():
    with patch("server.get_vector_store", return_value=None):
        r = client.get("/api/kb")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_kb_upload_requires_embedding():
    with patch("server.get_vector_store", return_value=None):
        r = client.post("/api/kb/upload", data={"collection": "default"},
                        files={"files": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_kb_search_requires_embedding():
    with patch("server.get_vector_store", return_value=None):
        r = client.post("/api/kb/search", json={"collection": "default", "query": "x"})
    assert r.status_code == 400


# ---------- 设置 ----------
def test_settings_save_and_read(tmp_path, monkeypatch):
    import server as srv
    monkeypatch.setattr(srv, "update_env", lambda d, path=None: None)
    monkeypatch.setattr(srv, "save_prefs", lambda p, m, cm=None: None)
    monkeypatch.setattr(srv, "load_prefs", lambda: {"provider": "deepseek", "model": "m", "custom_models": {}})
    r = client.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-chat"})
    assert r.status_code == 200
    assert r.json()["model"] == "deepseek-chat"
