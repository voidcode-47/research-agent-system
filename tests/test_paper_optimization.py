# ruff: noqa: F401, F811 — pytest fixture 通过导入注入，ruff 会误判为重定义
import contextvars
import json
import threading

import pytest

from llm.base import ChatResponse
from paper import analysis, similar
from paper.rewrite import rewrite_paragraph
from api import paper as api_paper
from paper import store
from utils import quota
from tests.test_paper_api import env, _upload_ok, _confirm


def test_diagnosis_overlaps_independent_dimensions_and_copies_context(env, tmp_path, monkeypatch):
    pid = _upload_ok(tmp_path)
    _confirm(pid)
    barrier = threading.Barrier(2)
    marker = contextvars.ContextVar("diagnosis-test", default="missing")
    marker.set("reserved-task")

    def handler(*args):
        assert marker.get() == "reserved-task"
        barrier.wait(timeout=1)
        return {"score": 80, "summary": "ok", "stale": False}

    monkeypatch.setitem(analysis._HANDLERS, "level", handler)
    monkeypatch.setitem(analysis._HANDLERS, "feasibility", handler)
    report = analysis.diagnose(pid, env, ["level", "feasibility"])
    assert report["errors"] == {}
    assert set(report["dimensions"]) == {"level", "feasibility"}


def test_format_diagnosis_does_not_wait_for_a_model(env):
    before = len(env.calls)
    result = analysis._diagnose_format(None, {"paragraphs": []}, {}, "p_test")
    assert len(env.calls) == before
    assert result["llm_error"] == ""


@pytest.mark.parametrize("original,revised", [
    ("This study compares test generation methods on three public datasets.", "本研究比较了三个公开数据集上的测试生成方法。"),
    ("本研究比较了三个公开数据集上的测试生成方法。", "This study compares test generation methods on three public datasets."),
])
def test_rewrite_rejects_translation_and_retries_in_original_language(env, original, revised):
    calls = []

    def chat(messages, **kwargs):
        calls.append(messages)
        text = revised if len(calls) == 1 else original
        return ChatResponse(content=json.dumps({"revised": text, "reasons": []}))

    env.chat = chat
    result = rewrite_paragraph(env, original, {}, instruction="请用中文解释改动理由")
    assert len(calls) == 2
    assert result["revised"] == original


def test_rewrite_never_exposes_repeated_translation(env):
    env.payload["revised"] = "这是一段错误翻译的中文内容，应该被拒绝。"
    with pytest.raises(RuntimeError, match="原文语言"):
        rewrite_paragraph(env, "This study evaluates three test generation methods on public datasets.", {})


def test_similar_search_overlaps_sources_and_keeps_results(monkeypatch):
    barrier = threading.Barrier(2)

    def search(self, term, limit):
        barrier.wait(timeout=1)
        return [{"title": "Software testing", "doi": "10.1/test"}]

    monkeypatch.setattr(similar.AcademicSearchTool, "_search_crossref", search)
    monkeypatch.setattr(similar.AcademicSearchTool, "_search_openalex", search)
    report = similar.find_similar({"title": "Software testing"})
    assert len(report["items"]) == 1
    assert not report.get("error")


def test_cloud_batch_rewrite_overlaps_calls_and_preserves_order(env, tmp_path, monkeypatch):
    pid = _upload_ok(tmp_path)
    _confirm(pid)
    ids = [p["id"] for p in store.current_paragraphs(pid) if p["type"] == "body"][:2]
    barrier = threading.Barrier(2)

    def rewrite(llm, text, requirement, **kwargs):
        barrier.wait(timeout=1)
        return {"revised": text, "original": text}

    monkeypatch.setattr(api_paper, "rewrite_paragraph", rewrite)
    tid = api_paper._new_task("rewrite", pid, "batch-parallel")
    api_paper._batch_worker(tid, pid, ids, "deai", "", False)
    report = api_paper.PAPER_TASKS[tid]["report"]
    assert report["errors"] == []
    assert [item["paragraph_id"] for item in report["items"]] == ids


def test_parallel_diagnosis_uses_shared_quota_reservation(env, tmp_path, monkeypatch):
    from config.settings import settings
    pid = _upload_ok(tmp_path)
    _confirm(pid)
    monkeypatch.setattr(settings, "DAILY_QUOTA", 3)
    reservation = quota.reserve(3)
    token = quota.set_reservation(reservation)
    try:
        report = analysis.diagnose(pid, env)
        assert report["errors"] == {}
        assert set(report["dimensions"]) == {"level", "feasibility", "format", "deai"}
        assert quota.usage_snapshot()["used"] <= 3
    finally:
        quota.reset_reservation(token)
        quota.release(reservation)
