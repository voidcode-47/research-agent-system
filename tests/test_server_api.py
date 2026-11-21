# -*- coding: utf-8 -*-
"""server.py（FastAPI Web 服务）API 层测试。"""
from unittest.mock import patch
from urllib.parse import quote
import threading

import pytest

import server
from fastapi.testclient import TestClient
from utils import auth, prefs
from utils import custom_providers

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def _admin_session(tmp_path, monkeypatch):
    """设置类接口仅管理员可用；这里直接注入管理员会话。"""
    monkeypatch.setattr(auth, "ADMIN_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))
    monkeypatch.setattr(custom_providers, "CUSTOM_PROVIDERS_PATH", str(tmp_path / "custom_providers.json"))
    monkeypatch.setattr(custom_providers, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(server.settings, "CUSTOM_API_BASE_URL", "")
    monkeypatch.setattr(server.settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    auth.reset_state()
    client.cookies.set(auth.COOKIE_NAME, auth.create_session("admin"))
    yield
    client.cookies.clear()
    auth.reset_state()


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
def test_switch_custom_model_updates_saved_effective_and_factory_model(monkeypatch):
    from llm.factory import LLMFactory
    from unittest.mock import MagicMock
    monkeypatch.setattr("llm.openai_llm.OpenAI", MagicMock())
    created = client.post("/api/custom-providers", json={
        "name": "Test gateway", "protocol": "openai",
        "base_url": "https://example.test/v1", "models": ["model-a", "model-b"],
        "default_model": "model-a",
    })
    assert created.status_code == 200
    provider = "custom:" + created.json()["id"]
    for model in ["model-a", "model-b", "model-a"]:
        response = client.post("/api/settings", json={"provider": provider, "model": model})
        assert response.status_code == 200
        assert prefs.load_prefs()["model"] == model
        assert client.get("/api/providers").json()["effective_model"] == model
        assert LLMFactory.create().model == model


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_providers():
    r = client.get("/api/providers")
    assert r.status_code == 200
    data = r.json()
    assert data["providers"] == ["ollama", "lmstudio"]
    assert "labels" in data


def test_models_known_provider():
    r = client.get("/api/models?provider=ollama")
    assert r.status_code == 200
    assert "qwen2.5:7b" in r.json()["models"]


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


def test_report_word_download_preserves_chinese_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    name = "研究报告 中文.md"
    (tmp_path / name).write_text("# 中文报告\n\n正文", encoding="utf-8")

    response = client.get(f"/api/reports/{quote(name)}?format=docx")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f"attachment; filename*=UTF-8''{quote('研究报告 中文.docx')}"
    )
    assert response.content.startswith(b"PK")


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


# ---------- 研究追问 ----------
def test_research_followup_streams():
    fake = _FakeStreamLLM()
    with patch("server.LLMFactory.create", return_value=fake):
        r = client.post("/api/research/followup", json={
            "report": "# 报告\\n内容",
            "question": "展开讲讲",
        })
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "你好" in r.text
    assert '"done"' in r.text


def test_research_followup_requires_input():
    r = client.post("/api/research/followup", json={"report": "", "question": "q"})
    assert r.status_code == 400


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
    """失败报告使用统一 error 终态且不带 report。"""
    done = threading.Event()
    def _fake_worker(task_id, req):
        server.paper_api._fail(server.RESEARCH_TASKS[task_id], RuntimeError("API 限流：429"))
        done.set()

    with patch("server._run_research_worker", _fake_worker):
        r = client.post("/api/research", json={"topic": "t"})
        assert done.wait(3)
    st = client.get(f"/api/research/{r.json()['task_id']}").json()
    assert st["status"] == "error"
    assert st["report"] is None


# ---------- 设置 ----------
def test_settings_save_and_read(tmp_path, monkeypatch):
    import server as srv
    monkeypatch.setattr(server, "update_env", lambda d, path=None: None)
    monkeypatch.setattr(srv, "save_prefs", lambda p, m, cm=None: None)
    monkeypatch.setattr(srv, "load_prefs", lambda: {"provider": "ollama", "model": "m", "custom_models": {}})
    r = client.post("/api/settings", json={"provider": "ollama", "model": "qwen2.5:7b"})
    assert r.status_code == 200
    assert r.json()["model"] == "qwen2.5:7b"


# ---------- 任务表维护 ----------
def test_research_tasks_are_pruned(monkeypatch):
    monkeypatch.setattr(server, "MAX_RESEARCH_TASKS", 3)
    for i in range(5):
        server.RESEARCH_TASKS[f"t{i}"] = {
            "status": "done",
            "created_at": f"2024-01-0{i + 1} 00:00:00",
        }
    server.RESEARCH_TASKS["live"] = {
        "status": "running",
        "created_at": "2024-02-01 00:00:00",
    }

    server._prune_research_tasks()

    assert len(server.RESEARCH_TASKS) == 3
    assert "live" in server.RESEARCH_TASKS      # 运行中的任务不动
    assert "t4" in server.RESEARCH_TASKS        # 保留最新的已结束任务
    assert "t0" not in server.RESEARCH_TASKS    # 最旧的已结束任务被清理


def test_research_tasks_under_limit_untouched(monkeypatch):
    monkeypatch.setattr(server, "MAX_RESEARCH_TASKS", 10)
    server.RESEARCH_TASKS["a"] = {"status": "done", "created_at": "2024-01-01 00:00:00"}
    server._prune_research_tasks()
    assert "a" in server.RESEARCH_TASKS


def test_settings_local_base_url_saves(tmp_path, monkeypatch):
    """本地服务地址（Ollama/LM Studio）：写 .env 并热更新 settings，去尾部斜杠。"""
    calls = []
    monkeypatch.setattr(server, "update_env", lambda d, path=None: calls.append(dict(d)))
    monkeypatch.setattr(server.settings, "OLLAMA_BASE_URL", "")
    r = client.post("/api/settings", json={"provider": "ollama",
                                           "ollama_base_url": "http://127.0.0.1:11434/"})
    assert r.status_code == 200
    assert calls and calls[0]["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    assert server.settings.OLLAMA_BASE_URL == "http://127.0.0.1:11434"
