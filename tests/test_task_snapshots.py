"""A queued paper operation belongs to the inputs and model it was created for."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from api import paper as api_paper
from config.settings import settings
from paper import store
from utils import prefs, quota


@pytest.fixture
def queued_paper(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(settings, "DAILY_QUOTA", 100)
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    selection = {"provider": "ollama", "model": "snapshot-model-a"}
    monkeypatch.setattr(prefs, "load_prefs", lambda: dict(selection))
    monkeypatch.setattr(api_paper.LLMFactory, "create", lambda provider=None, model=None, **kw:
                        SimpleNamespace(provider=provider, model=model, is_local=True))
    scheduled = {}

    def hold(task_id, function, *args, **kwargs):
        scheduled[task_id] = (function, args, kwargs)

    monkeypatch.setattr(api_paper, "_spawn", hold)
    api_paper.PAPER_TASKS.clear()
    paper_id = store.new_paper_id()
    store.create_paper_dir(paper_id)
    paragraphs = [{
        "id": "p_0001", "type": "body", "page": 1,
        "text": "This study compares software testing methods on three public datasets.",
    }]
    store.save_meta(paper_id, {
        "filename": "snapshot.pdf", "source_type": "pdf", "title": "Model snapshot",
        "current_version": 0,
    })
    store.save_parsed(paper_id, {"paragraphs": paragraphs, "sections": []})
    store.save_version(paper_id, paragraphs)
    requirement = {
        "level": "undergraduate", "confirmed": True,
        "format": {"template": "generic_thesis", "reference_style": "gbt7714"},
        "deai": {"strength": "medium", "keep": [], "banned_phrases": []},
        "notes": "",
    }
    store.save_requirement(paper_id, requirement)
    state = SimpleNamespace(
        paper_id=paper_id, selection=selection, scheduled=scheduled,
        client=TestClient(server.app), requirement=requirement,
    )
    yield state
    state.client.close()
    api_paper.PAPER_TASKS.clear()


def _start(state, operation):
    if operation == "rewrite":
        endpoint, body = "rewrite", {"paragraph_id": "p_0001", "use_guideline": False}
    elif operation == "diagnose":
        endpoint, body = "diagnose", {"dimensions": ["level"]}
    else:
        endpoint, body = "rewrite/guideline", {}
    response = state.client.post(f"/api/paper/{state.paper_id}/{endpoint}", json=body)
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


def _change(state, field):
    if field == "model":
        state.selection["model"] = "snapshot-model-b"
    elif field == "requirement":
        response = state.client.put(
            f"/api/paper/{state.paper_id}/requirement",
            json={**state.requirement, "notes": "Focus on the experimental design."},
        )
        assert response.status_code == 200, response.text
    else:
        response = state.client.post(f"/api/paper/{state.paper_id}/apply", json={
            "paragraph_id": "p_0001",
            "revised": "The revised study evaluates testing methods on four public datasets.",
            "expected_version": 0,
        })
        assert response.status_code == 200, response.text


def _run_scheduled(state, task_id):
    function, args, kwargs = state.scheduled[task_id]
    api_paper._run_task(task_id, lambda: function(*args, **kwargs))
    return api_paper.PAPER_TASKS[task_id]


@pytest.mark.parametrize("operation", ["rewrite", "diagnose", "guideline"])
@pytest.mark.parametrize("changed_input", ["body", "requirement", "model"])
def test_task_dedup_only_reuses_identical_inputs(queued_paper, operation, changed_input):
    first = _start(queued_paper, operation)
    assert _start(queued_paper, operation) == first

    _change(queued_paper, changed_input)

    assert _start(queued_paper, operation) != first


def test_queued_rewrite_uses_model_selected_when_submitted(queued_paper, monkeypatch):
    seen = []

    def rewrite(llm, text, requirement, **kwargs):
        seen.append(llm.model)
        return {"original": text, "revised": text, "source_version": kwargs["version"]}

    monkeypatch.setattr(api_paper, "rewrite_paragraph", rewrite)
    task_id = _start(queued_paper, "rewrite")
    _change(queued_paper, "model")

    task = _run_scheduled(queued_paper, task_id)

    assert task["status"] == "done", task.get("error")
    assert seen == ["snapshot-model-a"]


@pytest.mark.parametrize("changed_input", ["body", "requirement"])
def test_guideline_discards_result_if_source_changes_during_generation(
        queued_paper, monkeypatch, changed_input):
    def generate(llm, parsed, requirement, paragraphs, **kwargs):
        _change(queued_paper, changed_input)
        return {"summary": "A guide for the old source.", "version": kwargs["version"]}

    monkeypatch.setattr(api_paper, "generate_guideline", generate)
    task_id = _start(queued_paper, "guideline")

    task = _run_scheduled(queued_paper, task_id)

    assert task["status"] == "error"
    assert store.load_rewrite_guide(queued_paper.paper_id) is None
