# -*- coding: utf-8 -*-
"""任务级额度预留：创建、复用、取消、终态释放与执行上下文。"""
import threading

import pytest
from fastapi import HTTPException

from api import paper as api_paper
from config.settings import settings
from paper import store
from utils import quota


@pytest.fixture(autouse=True)
def quota_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store.settings, "PAPER_DATA_DIR",
                        str(tmp_path / "papers"))
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "_today", lambda: "2026-01-02")
    monkeypatch.setattr(settings, "DAILY_QUOTA", 3)
    quota._reservations.clear()
    api_paper.PAPER_TASKS.clear()
    yield
    quota._reservations.clear()
    api_paper.PAPER_TASKS.clear()


def _noop_spawn(monkeypatch):
    monkeypatch.setattr(api_paper, "_spawn", lambda tid, fn, *a: None)


def test_launch_reserves_quota_and_finish_releases_it(monkeypatch):
    _noop_spawn(monkeypatch)
    task_id = api_paper._launch(
        "rewrite", "p_test", "k1", lambda *a: None, quota_amount=2,
    )

    snap = quota.usage_snapshot()
    assert snap["used"] == 0
    assert snap["reserved"] == 2
    assert snap["remaining"] == 1

    api_paper._finish(api_paper.PAPER_TASKS[task_id], "done")
    snap = quota.usage_snapshot()
    assert snap["reserved"] == 0
    assert snap["remaining"] == 3


def test_reused_task_does_not_reserve_twice(monkeypatch):
    _noop_spawn(monkeypatch)
    first = api_paper._launch(
        "diagnose", "p_test", "same", lambda *a: None, quota_amount=2,
    )
    second = api_paper._launch(
        "diagnose", "p_test", "same", lambda *a: None, quota_amount=2,
    )

    assert first == second
    assert quota.usage_snapshot()["reserved"] == 2
    assert api_paper._finish(api_paper.PAPER_TASKS[first], "cancelled") is True
    assert quota.usage_snapshot()["reserved"] == 0


def test_launch_rejects_insufficient_quota_before_task_creation(monkeypatch):
    _noop_spawn(monkeypatch)
    quota.reserve(2)
    cleaned = []

    with pytest.raises(HTTPException) as ei:
        api_paper._launch(
            "upload", "p_test", "", lambda *a: None,
            cleanup=lambda: cleaned.append(1), quota_amount=2,
        )

    assert ei.value.status_code == 429
    assert "需要 2 次模型调用" in ei.value.detail
    assert "仅剩 1 次" in ei.value.detail
    assert cleaned == [1]
    assert api_paper.PAPER_TASKS == {}
    assert quota.usage_snapshot()["used"] == 0


def test_queued_cancel_releases_reservation(monkeypatch):
    _noop_spawn(monkeypatch)
    task_id = api_paper._launch(
        "guideline", "p_test", "guide", lambda *a: None, quota_amount=1,
    )
    assert quota.usage_snapshot()["reserved"] == 1

    assert api_paper._cancel_task(
        api_paper.PAPER_TASKS[task_id], message="已取消"
    ) is True
    assert api_paper.PAPER_TASKS[task_id]["status"] == "cancelled"
    assert quota.usage_snapshot()["reserved"] == 0


def test_worker_runs_with_task_reservation_context():
    token = quota.reserve(2)
    task_id, _ = api_paper._reserve_task("rewrite", "p_test", "ctx")
    api_paper.PAPER_TASKS[task_id]["quota_token"] = token

    seen = []
    done = threading.Event()

    def worker(tid, paper_id):
        quota.consume()
        seen.append(quota.usage_snapshot()["used"])
        api_paper._finish(api_paper.PAPER_TASKS[tid], "done")
        done.set()

    api_paper._spawn(task_id, worker, task_id, "p_test")
    assert done.wait(5)
    assert seen == [1]
    snap = quota.usage_snapshot()
    assert snap["used"] == 1
    assert snap["reserved"] == 0
