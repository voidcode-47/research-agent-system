# -*- coding: utf-8 -*-
"""任务 SSE 通知。"""
import json

import pytest
from fastapi.testclient import TestClient

import api.paper as api_paper
import server
from config.settings import settings

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def clean_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    api_paper.PAPER_TASKS.clear()
    yield
    api_paper.PAPER_TASKS.clear()


def _done_task(paper_id="p_test"):
    task_id, _ = api_paper._reserve_task("rewrite", paper_id, "events")
    api_paper._finish(
        api_paper.PAPER_TASKS[task_id], "done",
        result={"paragraph_id": "p_0001", "revised": "新正文"},
    )
    return task_id


def test_terminal_task_stream_sends_data_and_end_event():
    task_id = _done_task()
    with client.stream(
        "GET", f"/api/paper/p_test/tasks/{task_id}/events"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    data_lines = [
        line[len("data: "):] for line in body.splitlines()
        if line.startswith("data: ") and line != "data: {}"
    ]
    assert data_lines
    view = json.loads(data_lines[0])
    assert view["status"] == "done"
    assert view["result"]["paragraph_id"] == "p_0001"
    assert "event: end" in body


def test_events_reject_unknown_or_foreign_task():
    task_id = _done_task()
    assert client.get(
        f"/api/paper/p_other/tasks/{task_id}/events"
    ).status_code == 404
    assert client.get(
        "/api/paper/p_test/tasks/not-a-task/events"
    ).status_code == 404
