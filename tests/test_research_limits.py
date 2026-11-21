"""Research generation shares quota, identity, capacity and cancellation rules."""
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from api import paper as paper_api
from config.settings import settings
from llm.base import ChatResponse
from utils import auth, quota


REPORT = "## Research design\n\n" + "The method compares a baseline with the proposed model. [1]\n" * 8
REFS = [{"title": "Methods study", "authors": "Example Author", "year": 2024,
         "journal": "Methods Journal", "doi": "10.0000/example", "abstract": "Comparison methods."}]


@pytest.fixture
def research_env(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr(server, "REPORT_DIR", str(report_dir))
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(settings, "DAILY_QUOTA", 20)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "review-admin-password")
    monkeypatch.setattr(auth, "ADMIN_PATH", str(tmp_path / "admin.json"))
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", str(tmp_path / "credentials.txt"))
    monkeypatch.setattr(server, "load_prefs", lambda: {"provider": "fake", "model": "model"})
    monkeypatch.setattr(server, "_search_references", lambda *a: (REFS, {"Crossref": 1}, []))
    auth.reset_state()
    server.RESEARCH_TASKS.clear()
    paper_api.PAPER_TASKS.clear()

    entered_content = threading.Event()
    release_content = threading.Event()
    finished = set()
    running = set()
    condition = threading.Condition()

    class DelayedContent(str):
        def strip(self, *args, **kwargs):
            entered_content.set()
            assert release_content.wait(5), "test did not release report processing"
            return super().strip(*args, **kwargs)

    class FakeModel:
        delay_content = False
        stream_failure = False
        partial_stream = False

        def __init__(self):
            self.chat_admin = []
            self.stream_admin = []

        def chat(self, messages, **kwargs):
            self.chat_admin.append(auth.is_admin())
            content = DelayedContent(REPORT) if self.delay_content else REPORT
            return ChatResponse(content=content, usage={"total_tokens": 12})

        def stream_chat(self, messages, **kwargs):
            self.stream_admin.append(auth.is_admin())
            if self.partial_stream:
                yield "A partial answer"
            if self.stream_failure:
                raise ConnectionError("simulated connection loss")
            if not self.partial_stream:
                yield "An evidence-based answer. [1]"

    model = FakeModel()
    monkeypatch.setattr(server.LLMFactory, "create", lambda *a, **k: model)
    original_worker = server._run_research_worker

    def tracked_worker(task_id, *args, **kwargs):
        with condition:
            running.add(task_id)
        try:
            return original_worker(task_id, *args, **kwargs)
        finally:
            with condition:
                finished.add(task_id)
                condition.notify_all()

    monkeypatch.setattr(server, "_run_research_worker", tracked_worker)

    def wait_finished(task_id):
        with condition:
            assert condition.wait_for(lambda: task_id in finished, timeout=5), "research worker did not finish"

    client = TestClient(server.app)
    env = SimpleNamespace(client=client, model=model, report_dir=report_dir,
                          entered_content=entered_content, release_content=release_content,
                          wait_finished=wait_finished)
    yield env
    release_content.set()
    for task_id in list(server.RESEARCH_TASKS):
        client.post(f"/api/research/{task_id}/cancel")
    with condition:
        assert condition.wait_for(lambda: running <= finished, timeout=5), "research test leaked a worker"
    for task in list(paper_api.PAPER_TASKS.values()):
        paper_api._cancel_task(task, immediate=True)
    server.RESEARCH_TASKS.clear()
    paper_api.PAPER_TASKS.clear()
    auth.reset_state()
    client.close()


def _login(env):
    response = env.client.post("/api/auth/login", json={
        "username": "admin", "password": "review-admin-password",
    })
    assert response.status_code == 200


def _event_types(response):
    return [json.loads(line[5:].strip()).get("type")
            for line in response.text.splitlines() if line.startswith("data:")]


def test_research_rejects_zero_quota_before_starting_model(research_env, monkeypatch):
    env = research_env
    monkeypatch.setattr(settings, "DAILY_QUOTA", 0)
    response = env.client.post("/api/research", json={"topic": "Software testing methods"})
    if response.status_code == 200:
        env.wait_finished(response.json()["task_id"])
    assert response.status_code == 429
    assert env.model.chat_admin == []
    assert quota.usage_snapshot()["reserved"] == 0


def test_followup_rejects_zero_quota_before_streaming(research_env, monkeypatch):
    env = research_env
    monkeypatch.setattr(settings, "DAILY_QUOTA", 0)
    response = env.client.post("/api/research/followup", json={"report": REPORT, "question": "What is the method?"})
    assert response.status_code == 429
    assert env.model.stream_admin == []
    assert quota.usage_snapshot()["reserved"] == 0


def test_research_background_worker_keeps_admin_identity(research_env, monkeypatch):
    env = research_env
    monkeypatch.setattr(settings, "DAILY_QUOTA", 0)
    _login(env)
    response = env.client.post("/api/research", json={"topic": "Software testing methods"})
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    env.wait_finished(task_id)
    assert env.client.get(f"/api/research/{task_id}").json()["status"] == "done"
    assert env.model.chat_admin == [True]
    assert quota.usage_snapshot()["used"] == 0
    assert quota.usage_snapshot()["reserved"] == 0


def test_followup_admin_exemption_does_not_charge_other_users(research_env, monkeypatch):
    env = research_env
    monkeypatch.setattr(settings, "DAILY_QUOTA", 0)
    _login(env)
    response = env.client.post("/api/research/followup", json={"report": REPORT, "question": "What is the method?"})
    assert response.status_code == 200
    assert "done" in _event_types(response)
    assert env.model.stream_admin == [True]
    assert quota.usage_snapshot()["used"] == 0


def test_research_obeys_shared_active_task_limit_without_leaking_quota(research_env, monkeypatch):
    env = research_env
    monkeypatch.setattr(settings, "PAPER_MAX_ACTIVE_TASKS", 1)
    monkeypatch.setattr(paper_api, "MAX_ACTIVE_TASKS", 1)
    occupied, _ = paper_api._reserve_task("rewrite", "p_test", "occupied")
    token = quota.reserve(1)
    paper_api.PAPER_TASKS[occupied]["quota_token"] = token
    response = env.client.post("/api/research", json={"topic": "Software testing methods"})
    if response.status_code == 200:
        env.wait_finished(response.json()["task_id"])
    assert response.status_code == 429
    assert env.model.chat_admin == []
    assert quota.usage_snapshot()["reserved"] == 1


def test_cancel_after_model_return_cannot_be_overwritten_by_save(research_env):
    env = research_env
    env.model.delay_content = True
    response = env.client.post("/api/research", json={"topic": "Software testing methods"})
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert env.entered_content.wait(5)
    cancel = env.client.post(f"/api/research/{task_id}/cancel")
    assert cancel.status_code == 200
    env.release_content.set()
    env.wait_finished(task_id)
    task = env.client.get(f"/api/research/{task_id}").json()
    assert task["status"] == "cancelled"
    assert task["report"] is None
    assert task["report_file"] is None
    assert list(env.report_dir.iterdir()) == []
    assert quota.usage_snapshot()["reserved"] == 0


def test_followup_success_charges_one_logical_call(research_env):
    env = research_env
    response = env.client.post("/api/research/followup", json={"report": REPORT, "question": "What is the method?"})
    assert response.status_code == 200
    assert "done" in _event_types(response)
    assert quota.usage_snapshot()["used"] == 1
    assert quota.usage_snapshot()["reserved"] == 0


def test_followup_transport_failure_refunds_unused_call(research_env):
    env = research_env
    env.model.stream_failure = True
    response = env.client.post("/api/research/followup", json={"report": REPORT, "question": "What is the method?"})
    assert response.status_code == 200
    assert "error" in _event_types(response)
    assert "done" not in _event_types(response)
    assert quota.usage_snapshot()["used"] == 0
    assert quota.usage_snapshot()["reserved"] == 0


def test_followup_partial_output_failure_keeps_charge_and_error_terminal(research_env):
    env = research_env
    env.model.stream_failure = True
    env.model.partial_stream = True
    response = env.client.post("/api/research/followup", json={"report": REPORT, "question": "What is the method?"})
    assert response.status_code == 200
    assert "A partial answer" in response.text
    assert "error" in _event_types(response)
    assert "done" not in _event_types(response)
    assert quota.usage_snapshot()["used"] == 1
    assert quota.usage_snapshot()["reserved"] == 0
