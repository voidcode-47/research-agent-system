"""任务并发、队列、保留时长与每日额度配置。"""
import datetime

import pytest
from pydantic import ValidationError

from api import paper as api_paper
from config.settings import Settings, settings
from paper import store
from utils import quota


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store.settings, "PAPER_DATA_DIR",
                        str(tmp_path / "papers"))
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    api_paper.PAPER_TASKS.clear()
    yield tmp_path
    api_paper.PAPER_TASKS.clear()


def _terminal(task_id: str, finished_at: str, status: str = "done") -> dict:
    return {
        "id": task_id,
        "kind": "test",
        "paper_id": "p_test",
        "status": status,
        "created_at": finished_at,
        "updated_at": "00:00:00",
        "finished_at": finished_at,
    }


def test_runtime_limits_follow_settings():
    assert api_paper.TASK_WORKERS == settings.PAPER_TASK_WORKERS
    assert api_paper.MAX_ACTIVE_TASKS == settings.PAPER_MAX_ACTIVE_TASKS
    assert (api_paper.TASK_RETENTION_MINUTES
            == settings.PAPER_TASK_RETENTION_MINUTES)
    assert api_paper._TASK_QUEUE.maxsize == settings.PAPER_MAX_ACTIVE_TASKS


def test_env_overrides_are_loaded(monkeypatch):
    monkeypatch.setenv("PAPER_TASK_WORKERS", "2")
    monkeypatch.setenv("PAPER_MAX_ACTIVE_TASKS", "4")
    monkeypatch.setenv("PAPER_TASK_RETENTION_MINUTES", "30")
    monkeypatch.setenv("DAILY_QUOTA", "7")

    loaded = Settings(_env_file=None)

    assert loaded.PAPER_TASK_WORKERS == 2
    assert loaded.PAPER_MAX_ACTIVE_TASKS == 4
    assert loaded.PAPER_TASK_RETENTION_MINUTES == 30
    assert loaded.DAILY_QUOTA == 7


@pytest.mark.parametrize("name,value", [
    ("PAPER_TASK_WORKERS", "0"),
    ("PAPER_TASK_WORKERS", "17"),
    ("PAPER_MAX_ACTIVE_TASKS", "0"),
    ("PAPER_MAX_ACTIVE_TASKS", "101"),
    ("PAPER_TASK_RETENTION_MINUTES", "-1"),
    ("PAPER_TASK_RETENTION_MINUTES", "525601"),
    ("DAILY_QUOTA", "-1"),
])
def test_invalid_limits_are_rejected(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_worker_count_uses_configured_value(monkeypatch):
    names = []

    class FakeThread:
        def __init__(self, **kwargs):
            names.append(kwargs["name"])

        def start(self):
            pass

    previous = api_paper._workers_started
    api_paper._workers_started = False
    monkeypatch.setattr(api_paper.threading, "Thread", FakeThread)
    monkeypatch.setattr(api_paper, "TASK_WORKERS", 2)
    try:
        api_paper._ensure_workers()
        assert names == ["paper-task-0", "paper-task-1"]
    finally:
        api_paper._workers_started = previous


def test_retention_prunes_only_expired_terminal_tasks(
        task_env, monkeypatch):
    monkeypatch.setattr(api_paper, "TASK_RETENTION_MINUTES", 60)
    now = datetime.datetime.now()
    old = (now - datetime.timedelta(minutes=61)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = (now - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    api_paper.PAPER_TASKS["old"] = _terminal("old", old)
    api_paper.PAPER_TASKS["fresh"] = _terminal("fresh", fresh)
    api_paper.PAPER_TASKS["bad_time"] = _terminal("bad_time", "not-a-date")
    api_paper.PAPER_TASKS["live"] = {
        **_terminal("live", old, status="running"),
        "finished_at": None,
    }

    api_paper._prune_locked()

    assert "old" not in api_paper.PAPER_TASKS
    assert {"fresh", "bad_time", "live"} <= set(api_paper.PAPER_TASKS)


def test_reserve_triggers_retention_cleanup(task_env, monkeypatch):
    monkeypatch.setattr(api_paper, "TASK_RETENTION_MINUTES", 60)
    old = (datetime.datetime.now() - datetime.timedelta(minutes=61)).strftime(
        "%Y-%m-%d %H:%M:%S")
    api_paper.PAPER_TASKS["old"] = _terminal("old", old)

    task_id, created = api_paper._reserve_task("rewrite", "p_test", "new")

    assert created is True
    assert task_id in api_paper.PAPER_TASKS
    assert "old" not in api_paper.PAPER_TASKS


def test_zero_retention_disables_time_cleanup(task_env, monkeypatch):
    monkeypatch.setattr(api_paper, "TASK_RETENTION_MINUTES", 0)
    old = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime(
        "%Y-%m-%d %H:%M:%S")
    api_paper.PAPER_TASKS["old"] = _terminal("old", old)

    api_paper._prune_locked()

    assert "old" in api_paper.PAPER_TASKS


def test_hard_count_limit_still_keeps_newest_terminal_tasks(
        task_env, monkeypatch):
    monkeypatch.setattr(api_paper, "TASK_RETENTION_MINUTES", 0)
    monkeypatch.setattr(api_paper, "MAX_PAPER_TASKS", 2)
    for idx in range(4):
        task_id = f"t{idx}"
        api_paper.PAPER_TASKS[task_id] = _terminal(
            task_id, f"2026-01-0{idx + 1} 00:00:00")
    api_paper.PAPER_TASKS["live"] = {
        **_terminal("live", "2026-01-01 00:00:00", status="running"),
        "finished_at": None,
    }

    api_paper._prune_locked()

    assert len(api_paper.PAPER_TASKS) == 2
    assert {"t3", "live"} == set(api_paper.PAPER_TASKS)


def test_options_exposes_effective_task_limits(task_env):
    from fastapi.testclient import TestClient

    from server import app

    response = TestClient(app).get("/api/paper/options")

    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert tasks == {
        "workers": api_paper.TASK_WORKERS,
        "max_active": api_paper.MAX_ACTIVE_TASKS,
        "retention_minutes": api_paper.TASK_RETENTION_MINUTES,
    }
