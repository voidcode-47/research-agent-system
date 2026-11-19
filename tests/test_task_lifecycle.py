"""任务生命周期：排队、取消、终态不可变、去重与队列上限。"""
import threading

import pytest

from api import paper as api_paper
from paper import store


@pytest.fixture(autouse=True)
def clean_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(store.settings, "PAPER_DATA_DIR",
                        str(tmp_path / "papers"))
    api_paper.PAPER_TASKS.clear()
    yield
    api_paper.PAPER_TASKS.clear()


def _task(task_id: str) -> dict:
    return api_paper.PAPER_TASKS[task_id]


def test_queued_task_runs_only_when_worker_starts():
    task_id, created = api_paper._reserve_task("rewrite", "p_test", "k1")
    assert created is True
    assert _task(task_id)["status"] == "queued"

    seen = []

    def runner():
        seen.append(_task(task_id)["status"])
        api_paper._finish(_task(task_id), "done")

    api_paper._run_task(task_id, runner)
    assert seen == ["running"]
    assert _task(task_id)["status"] == "done"


def test_spawn_dispatches_through_bounded_workers():
    task_id, _ = api_paper._reserve_task("test", "p_test", "worker")
    done = threading.Event()

    def worker(tid, paper_id):
        seen.append(_task(tid)["status"])
        api_paper._finish(_task(tid), "done")
        done.set()

    seen = []
    api_paper._spawn(task_id, worker, task_id, "p_test")
    assert done.wait(5)
    assert seen == ["running"]
    assert _task(task_id)["status"] == "done"


def test_cancel_queued_task_skips_work_and_runs_cleanup():
    cleaned = []
    task_id, _ = api_paper._reserve_task(
        "upload", "p_test", "", cleanup=lambda: cleaned.append(1)
    )
    assert api_paper._cancel_task(_task(task_id), message="已取消") is True

    called = []
    api_paper._run_task(task_id, lambda: called.append(1))
    assert called == []
    assert cleaned == [1]
    assert _task(task_id)["status"] == "cancelled"


def test_terminal_state_cannot_be_overwritten():
    task_id, _ = api_paper._reserve_task("rewrite", "p_test", "k2")
    task = _task(task_id)
    assert api_paper._finish(task, "done", result={"ok": True}) is True

    assert api_paper._finish(task, "error", error="late") is False
    api_paper._fail(task, RuntimeError("late"))
    assert api_paper._cancel_task(task) is False
    assert task["status"] == "done"
    assert task["error"] is None
    assert task["result"] == {"ok": True}


def test_active_tasks_dedup_and_respect_capacity(monkeypatch):
    monkeypatch.setattr(api_paper, "MAX_ACTIVE_TASKS", 2)

    first, created_a = api_paper._reserve_task("diagnose", "p_test", "same")
    second, created_b = api_paper._reserve_task("diagnose", "p_test", "same")
    assert first == second
    assert created_a is True
    assert created_b is False

    third, created_c = api_paper._reserve_task("diagnose", "p_test", "other")
    assert third != first
    assert created_c is True

    with pytest.raises(api_paper.TaskQueueFull):
        api_paper._reserve_task("diagnose", "p_test", "third")

    api_paper._finish(_task(first), "done")
    reused, created_d = api_paper._reserve_task("diagnose", "p_test", "same")
    assert reused != first
    assert created_d is True


def test_guideline_commit_blocks_cancel_until_terminal(monkeypatch):
    task_id, _ = api_paper._reserve_task("guideline", "p_test", "guide")
    task = _task(task_id)
    entered = threading.Event()
    release = threading.Event()
    saved = []

    def slow_save(paper_id, data):
        entered.set()
        assert release.wait(5)
        saved.append((paper_id, data))

    monkeypatch.setattr(api_paper.store, "save_rewrite_guide", slow_save)
    result = {}

    def commit():
        result["ok"] = api_paper._commit_guideline(
            task, "p_test", {"terms": ["统一术语"]}
        )

    worker = threading.Thread(target=commit)
    worker.start()
    assert entered.wait(5)

    cancelled = {}

    def cancel():
        api_paper._cancel_task(task, immediate=True, message="已取消")
        cancelled["status"] = task["status"]

    canceller = threading.Thread(target=cancel)
    canceller.start()
    release.set()
    worker.join(5)
    canceller.join(5)

    assert not worker.is_alive() and not canceller.is_alive()
    assert result["ok"] is True
    assert cancelled["status"] == "done"
    assert task["status"] == "done"
    assert len(saved) == 1
    assert task["guideline"] == {"terms": ["统一术语"]}


def test_guideline_commit_after_cancel_does_not_save(monkeypatch):
    task_id, _ = api_paper._reserve_task("guideline", "p_test", "guide2")
    task = _task(task_id)
    saved = []
    monkeypatch.setattr(
        api_paper.store, "save_rewrite_guide",
        lambda paper_id, data: saved.append((paper_id, data)),
    )

    api_paper._cancel_task(task, immediate=True, message="已取消")
    assert api_paper._commit_guideline(task, "p_test", {"terms": []}) is False
    assert saved == []
    assert task["status"] == "cancelled"
