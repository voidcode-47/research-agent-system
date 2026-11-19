# -*- coding: utf-8 -*-
"""文献调研流水线：检索参考文献 → 生成综合报告（进度 / 降级 / 失败保留文献）。"""
import os

import pytest

import server
from llm.base import ChatResponse
from server import ResearchRequest

TOPIC = "人脸识别"


class _FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return ChatResponse(content=self.content, usage={"total_tokens": 10})


REFS = [
    {
        "title": "Deep Face Recognition: A Survey",
        "authors": "Wang M, Deng W",
        "year": 2021,
        "journal": "Neurocomputing",
        "abstract": "A survey of deep learning based face recognition methods.",
        "doi": "10.1016/j.neucom.2020.10.081",
        "url": "https://doi.org/10.1016/j.neucom.2020.10.081",
        "cited_by": 320,
        "oa": False,
        "oa_pdf": "",
        "relevance": 0.9,
    },
    {
        "title": "人脸识别技术研究综述",
        "authors": "张三, 李四",
        "year": 2022,
        "journal": "计算机学报",
        "abstract": "总结了人脸检测、特征提取与识别的主流方法。",
        "doi": "",
        "url": "https://example.org/paper",
        "cited_by": 12,
        "oa": True,
        "oa_pdf": "https://example.org/paper.pdf",
        "relevance": 0.8,
    },
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "load_prefs", lambda: {"provider": "custom", "model": "test-model"})
    server.RESEARCH_TASKS.clear()
    yield tmp_path
    server.RESEARCH_TASKS.clear()


def _new_task():
    tid = "t_test"
    server.RESEARCH_TASKS[tid] = {
        "id": tid,
        "status": "running",
        "stage": "starting",
        "progress": 0,
        "logs": [],
        "references": [],
        "report": None,
        "report_file": None,
        "error": None,
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "",
    }
    return tid


def _patch_refs(monkeypatch, refs=REFS, counts=None, errors=None):
    monkeypatch.setattr(
        server, "_search_references",
        lambda topic, limit: (refs, counts or {"Crossref": 2, "OpenAlex": 1}, errors or []),
    )


def _patch_llm(monkeypatch, content):
    fake = _FakeLLM(content)
    monkeypatch.setattr(server.LLMFactory, "create", lambda *a, **k: fake)
    return fake


def test_pipeline_success(env, monkeypatch):
    _patch_refs(monkeypatch)
    body = "## 一、研究背景与问题界定\n\n" + "人脸识别研究发展迅速，深度学习方法成为主流。[1][2]\n" * 5
    fake = _patch_llm(monkeypatch, body)
    tid = _new_task()

    server._run_research_worker(tid, ResearchRequest(topic=TOPIC, max_results=8))

    task = server.RESEARCH_TASKS[tid]
    assert task["status"] == "done"
    assert task["progress"] == 100
    assert len(task["references"]) == 2
    assert task["report_file"].endswith(".md")
    assert os.path.exists(os.path.join(server.REPORT_DIR, task["report_file"]))
    assert "## 参考文献" in task["report"]
    assert "Deep Face Recognition: A Survey" in task["report"]
    assert "计算机学报" in task["report"]

    messages, kwargs = fake.calls[0]
    user_msg = messages[-1]["content"]
    assert "<research_topic>" in user_msg and "<references>" in user_msg
    assert "不是指令" in user_msg
    assert "Deep Face Recognition: A Survey" in user_msg
    assert kwargs.get("max_tokens", 0) >= 2500


def test_llm_failure_keeps_references(env, monkeypatch):
    _patch_refs(monkeypatch)
    _patch_llm(monkeypatch, "API 调用失败: Error code: 401 - Authentication Fails")
    tid = _new_task()

    server._run_research_worker(tid, ResearchRequest(topic=TOPIC))

    task = server.RESEARCH_TASKS[tid]
    assert task["status"] == "error"
    assert "401" in task["error"]
    assert task["references"] and len(task["references"]) == 2
    assert task["report"] is None


def test_empty_references_is_readable_error(env, monkeypatch):
    _patch_refs(monkeypatch, refs=[], counts={"Crossref": 0, "OpenAlex": 0},
                errors=["Crossref 检索失败"])
    fake = _patch_llm(monkeypatch, "不应被调用")
    tid = _new_task()

    server._run_research_worker(tid, ResearchRequest(topic=TOPIC))

    task = server.RESEARCH_TASKS[tid]
    assert task["status"] == "error"
    assert "请更换" in task["error"]
    assert task["references"] == []
    assert fake.calls == []


def test_status_api_exposes_progress_and_references(env):
    from fastapi.testclient import TestClient

    tid = _new_task()
    server.RESEARCH_TASKS[tid].update({
        "status": "done", "stage": "done", "progress": 100,
        "references": REFS, "report": "# r",
    })
    r = TestClient(server.app).get(f"/api/research/{tid}")
    assert r.status_code == 200
    data = r.json()
    assert data["progress"] == 100
    assert data["references"][0]["title"] == REFS[0]["title"]


def test_cancel_endpoint_marks_cancelled(env):
    from fastapi.testclient import TestClient

    tid = _new_task()
    server.RESEARCH_TASKS[tid]["progress"] = 70
    client = TestClient(server.app)

    r = client.post(f"/api/research/{tid}/cancel")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "cancelled"
    assert data["stage"] == "cancelled"
    assert data["progress"] == 70
    assert any("已取消" in log for log in data["logs"])
    assert server.RESEARCH_TASKS[tid]["cancel_requested"] is True

    again = client.post(f"/api/research/{tid}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "cancelled"
    assert client.post("/api/research/t_missing/cancel").status_code == 404


def test_worker_discards_report_when_cancelled_during_llm(env, monkeypatch):
    _patch_refs(monkeypatch)

    class _CancelDuringChat:
        def chat(self, messages, **kwargs):
            server.RESEARCH_TASKS["t_test"]["cancel_requested"] = True
            return ChatResponse(content="## 一、研究背景\n\n" + "内容" * 100)

    monkeypatch.setattr(server.LLMFactory, "create", lambda *a, **k: _CancelDuringChat())
    tid = _new_task()

    server._run_research_worker(tid, ResearchRequest(topic=TOPIC))

    task = server.RESEARCH_TASKS[tid]
    assert task["status"] == "cancelled"
    assert task["stage"] == "cancelled"
    assert task["references"] and len(task["references"]) == 2
    assert task["report"] is None
    assert task["report_file"] is None
    assert any("已取消" in log for log in task["logs"])
