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
    monkeypatch.setattr(server, "update_env", lambda d, path=None: None)
    monkeypatch.setattr(srv, "save_prefs", lambda p, m, cm=None: None)
    monkeypatch.setattr(srv, "load_prefs", lambda: {"provider": "deepseek", "model": "m", "custom_models": {}})
    r = client.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-chat"})
    assert r.status_code == 200
    assert r.json()["model"] == "deepseek-chat"


# ---------- 本轮修复回归 ----------
def test_kb_upload_is_sync_so_it_runs_off_event_loop():
    """必须是非 async 端点：否则 embedding 网络调用与磁盘 IO 会阻塞事件循环。"""
    import asyncio
    assert not asyncio.iscoroutinefunction(server.api_kb_upload)


def test_kb_upload_rejects_oversize_file(monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(server, "MAX_UPLOAD_MB", 1)
    with patch("server.get_vector_store", return_value=object()):
        r = client.post(
            "/api/kb/upload",
            files={"files": ("big.txt", b"x" * 5000, "text/plain")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["total_chunks"] == 0
    assert body["results"][0]["ok"] is False
    assert "上限" in body["results"][0]["error"]


def test_kb_upload_rejects_empty_file(monkeypatch):
    with patch("server.get_vector_store", return_value=object()):
        r = client.post(
            "/api/kb/upload",
            files={"files": ("empty.txt", b"", "text/plain")},
        )
    assert r.status_code == 200
    assert r.json()["results"][0]["ok"] is False


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


class _RecordingTool:
    name = "web_search"
    description = "d"
    parameters = {}

    def __init__(self):
        self.kwargs = None

    def to_openai_schema(self):
        return {}

    def execute(self, **kwargs):
        self.kwargs = kwargs
        return "ok"


def test_budget_search_tool_coerces_string_max_results():
    """工具参数来自 LLM 输出，可能是字符串，不能直接参与 min() 比较。"""
    inner = _RecordingTool()
    tool = server._BudgetSearchTool(inner, 5)

    tool.execute(query="x", max_results="3")
    assert inner.kwargs["max_results"] == 3

    tool.execute(query="x", max_results="abc")   # 非法值回退到预算
    assert inner.kwargs["max_results"] == 5

    tool.execute(query="x", max_results=99)      # 超预算被夹紧
    assert inner.kwargs["max_results"] == 5

    tool.execute(query="x", max_results=0)       # 0/None 回退到预算
    assert inner.kwargs["max_results"] == 5


# ---------- 知识库名称映射（中文名/短名 → Chroma 合法内部名） ----------
def test_kb_name_mapping_roundtrip(monkeypatch, tmp_path):
    """用户起的名字（中文/短名）与 Chroma 内部合法名双向转换。"""
    monkeypatch.setattr(server, "_KB_NAMES_PATH", str(tmp_path / "kb_names.json"))
    internal = "kb_ab12cd34ef"
    server._save_kb_names({internal: "01"})

    # 显示名 → 内部名：短名也能查回内部合法名
    assert server._kb_internal("01") == internal
    # 内部名 → 显示名：展示给用户的是起的名，不是 kb_xxx
    assert server._kb_display(internal) == "01"
    # 无映射的集合（如 default）原样返回，兼容旧数据
    assert server._kb_display("default") == "default"
    assert server._kb_internal("default") == "default"


def test_kb_name_mapping_unicode(monkeypatch, tmp_path):
    """中文知识库名：显示名保留中文，内部名始终是 Chroma 合法字符集。"""
    monkeypatch.setattr(server, "_KB_NAMES_PATH", str(tmp_path / "kb_names.json"))
    internal = "kb_9f8e7d6c5b"
    server._save_kb_names({internal: "我的知识库"})

    assert server._kb_internal("我的知识库") == internal
    assert server._kb_display(internal) == "我的知识库"


def test_kb_upload_returns_display_name(monkeypatch, tmp_path):
    """上传成功提示应显示用户起的名字（01），而不是内部名（kb_xxx）。"""
    monkeypatch.setattr(server, "_KB_NAMES_PATH", str(tmp_path / "kb_names.json"))
    internal = "kb_test123456"
    server._save_kb_names({internal: "01"})

    class _FakeVS:
        def __init__(self):
            self.called_with = None

        def add_documents(self, collection, chunks):
            self.called_with = collection

    vs = _FakeVS()
    with patch("server.get_vector_store", return_value=vs):
        r = client.post(
            "/api/kb/upload",
            data={"collection": "01"},
            files={"files": ("a.txt", "hello world", "text/plain")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["collection"] == "01"       # 前端提示显示显示名
    assert vs.called_with == internal       # 实际入库用 Chroma 合法内部名


# ---------- 场景模型池（每功能多模型配置） ----------
def test_settings_scene_pool_save_switch_and_fallback(tmp_path, monkeypatch):
    """模型池端到端：保存池 → 无激活顺延池首 → 切换激活 → 删激活项再顺延。"""
    import utils.prefs as prefs
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))
    monkeypatch.setattr(server, "update_env", lambda d, path=None: None)

    pool = [{"provider": "deepseek", "model": "deepseek-chat"},
            {"provider": "zhipu", "model": "glm-4-flash"}]
    r = client.post("/api/settings", json={"provider": "deepseek", "chat_models": pool})
    assert r.status_code == 200
    chat = prefs.load_prefs()["scene_models"]["chat"]
    assert chat["models"] == pool
    assert (chat["provider"], chat["model"]) == ("deepseek", "deepseek-chat")  # 顺延池首

    # 切换激活到 zhipu（池保持不变）
    r = client.post("/api/settings", json={"chat_provider": "zhipu", "chat_model": "glm-4-flash"})
    assert r.status_code == 200
    chat = prefs.load_prefs()["scene_models"]["chat"]
    assert (chat["provider"], chat["model"]) == ("zhipu", "glm-4-flash")
    assert len(chat["models"]) == 2

    # 删除当前激活项 → 激活顺延为剩余池首
    r = client.post("/api/settings", json={"chat_models": [pool[0]]})
    assert r.status_code == 200
    chat = prefs.load_prefs()["scene_models"]["chat"]
    assert chat["models"] == [pool[0]]
    assert (chat["provider"], chat["model"]) == ("deepseek", "deepseek-chat")


def test_settings_scene_pool_drops_invalid_and_empty_clears(tmp_path, monkeypatch):
    """非法提供商静默丢弃 + 去重；空池清空激活项。"""
    import utils.prefs as prefs
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))
    monkeypatch.setattr(server, "update_env", lambda d, path=None: None)

    r = client.post("/api/settings", json={"provider": "deepseek", "chat_models": [
        {"provider": "nope", "model": "x"},                    # 非法提供商 → 丢弃
        {"provider": "deepseek", "model": "deepseek-chat"},
        {"provider": "deepseek", "model": "deepseek-chat"},    # 重复 → 去重
    ]})
    assert r.status_code == 200
    chat = prefs.load_prefs()["scene_models"]["chat"]
    assert chat["models"] == [{"provider": "deepseek", "model": "deepseek-chat"}]

    r = client.post("/api/settings", json={"provider": "deepseek", "chat_models": []})
    assert r.status_code == 200
    chat = prefs.load_prefs()["scene_models"]["chat"]
    assert chat["models"] == []
    assert chat["provider"] == ""
    assert chat["model"] == ""


def test_settings_rejects_unknown_scene_provider(tmp_path, monkeypatch):
    """场景级非法提供商 → 400，不能写进 prefs（否则报错点远离操作）。"""
    import utils.prefs as prefs
    monkeypatch.setattr(prefs, "PREFS_PATH", str(tmp_path / "ui_prefs.json"))
    r = client.post("/api/settings", json={"provider": "deepseek", "chat_provider": "bogus"})
    assert r.status_code == 400
    assert "未知提供商" in r.json()["detail"]


def test_settings_local_base_url_saves(tmp_path, monkeypatch):
    """本地服务地址（Ollama/LM Studio）：写 .env 并热更新 settings，去尾部斜杠。"""
    calls = []
    monkeypatch.setattr(server, "update_env", lambda d, path=None: calls.append(dict(d)))
    monkeypatch.setattr(server.settings, "OLLAMA_BASE_URL", "")
    r = client.post("/api/settings", json={"provider": "deepseek",
                                           "ollama_base_url": "http://127.0.0.1:11434/"})
    assert r.status_code == 200
    assert calls and calls[0]["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    assert server.settings.OLLAMA_BASE_URL == "http://127.0.0.1:11434"
