"""任务日志持久化：活动任务落盘、重启恢复、损坏与写入失败。"""
import json
import os

import pytest

from api import paper as api_paper
from paper import store


@pytest.fixture(autouse=True)
def journal_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store.settings, "PAPER_DATA_DIR",
                        str(tmp_path / "papers"))
    api_paper.PAPER_TASKS.clear()
    yield
    api_paper.PAPER_TASKS.clear()


def _read_journal() -> dict:
    with open(api_paper.task_journal_path(), encoding="utf-8") as f:
        return json.load(f)


def _write_journal(data) -> None:
    path = api_paper.task_journal_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, ensure_ascii=False)


def test_active_task_is_persisted_and_removed_on_terminal():
    task_id, created = api_paper._reserve_task("rewrite", "p_test", "k1")
    assert created is True

    data = _read_journal()
    assert data["version"] == 1
    assert [item["id"] for item in data["tasks"]] == [task_id]
    record = data["tasks"][0]
    assert record["kind"] == "rewrite"
    assert record["paper_id"] == "p_test"
    assert record["status"] == "queued"
    assert set(record) == {
        "id", "kind", "paper_id", "status", "created_at", "updated_at",
    }

    api_paper._finish(api_paper.PAPER_TASKS[task_id], "done",
                      result={"text": "不应写入任务日志的正文"})
    data = _read_journal()
    assert data["tasks"] == []
    assert "不应写入任务日志的正文" not in json.dumps(data, ensure_ascii=False)


def test_restart_marks_active_tasks_as_interrupted_and_clears_journal():
    task_id, _ = api_paper._reserve_task("diagnose", "p_test", "same")

    api_paper.PAPER_TASKS.clear()
    assert api_paper.restore_task_journal() == 1

    task = api_paper.PAPER_TASKS[task_id]
    assert task["status"] == "error"
    assert task["stage"] == "error"
    assert task["error"] == api_paper._INTERRUPTED
    assert task["logs"] == [api_paper._INTERRUPTED]
    assert _read_journal()["tasks"] == []

    assert api_paper.restore_task_journal() == 0


def test_terminal_task_is_not_restored():
    task_id, _ = api_paper._reserve_task("rewrite", "p_test", "done")
    api_paper._finish(api_paper.PAPER_TASKS[task_id], "done")

    api_paper.PAPER_TASKS.clear()
    assert api_paper.restore_task_journal() == 0
    assert task_id not in api_paper.PAPER_TASKS


@pytest.mark.parametrize("payload", [
    "{broken json",
    {"version": 999, "tasks": [{"id": "aaaaaaaaaaaa"}]},
    {"version": 1, "tasks": "not-a-list"},
])
def test_invalid_journal_is_ignored(payload):
    _write_journal(payload)
    assert api_paper.restore_task_journal() == 0
    assert api_paper.PAPER_TASKS == {}


def test_invalid_task_ids_are_skipped():
    _write_journal({
        "version": 1,
        "tasks": [
            {"id": "../../escape", "kind": "rewrite"},
            {"id": "not-hex", "kind": "rewrite"},
            {"id": "aaaaaaaaaaaa", "kind": "rewrite", "paper_id": "p_test"},
        ],
    })

    assert api_paper.restore_task_journal() == 1
    assert list(api_paper.PAPER_TASKS) == ["aaaaaaaaaaaa"]
    assert api_paper.PAPER_TASKS["aaaaaaaaaaaa"]["status"] == "error"


def test_startup_restores_interrupted_task_through_status_api(monkeypatch):
    from fastapi.testclient import TestClient

    from server import app, auth

    task_id, _ = api_paper._reserve_task("upload", "p_test", "")
    api_paper.PAPER_TASKS.clear()
    monkeypatch.setattr(auth, "ensure_admin", dict)

    with TestClient(app) as client:
        response = client.get(f"/api/paper/upload/{task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["error"] == api_paper._INTERRUPTED


def test_startup_refuses_second_instance(monkeypatch):
    from fastapi.testclient import TestClient

    from server import app, auth
    from utils import runtime_lock

    monkeypatch.setattr(auth, "ensure_admin", dict)
    monkeypatch.setattr(runtime_lock, "acquire", lambda path: False)
    with pytest.raises(RuntimeError, match="另一个服务实例"), TestClient(app):
        pass


def test_journal_write_failure_does_not_break_task(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_json_file", fail)

    task_id, created = api_paper._reserve_task("rewrite", "p_test", "k2")
    assert created is True
    assert api_paper._finish(api_paper.PAPER_TASKS[task_id], "done") is True
    assert api_paper.PAPER_TASKS[task_id]["status"] == "done"
