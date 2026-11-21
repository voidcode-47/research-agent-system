"""模型结果与恢复数据必须通过完整契约校验后才能作为可用结果。"""

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

import api.paper as api_paper
import server
from config.settings import settings
from llm.base import ChatResponse
from paper import analysis, backup, store
from utils import quota


class Replies:
    is_local = True

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResponse(
            content=json.dumps(self.payloads.pop(0), ensure_ascii=False),
            usage={"total_tokens": 10},
        )


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(settings, "DAILY_QUOTA", 50)
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    pid = store.new_paper_id()
    directory = Path(store.create_paper_dir(pid))
    (directory / "original.pdf").write_bytes(b"%PDF-1.4\ncontract fixture\n")
    paragraphs = [
        {"id": "p_0001", "type": "body", "page": 1,
         "text": "实验样本量为 100 条，实验使用固定的训练集。"},
        {"id": "p_0002", "type": "body", "page": 2,
         "text": "实验对照组采用基线模型，记录实验准确率。"},
    ]
    store.save_meta(pid, {
        "filename": "contracts.pdf", "source_type": "pdf", "title": "实验研究",
        "paragraph_count": len(paragraphs), "current_version": 0,
    })
    store.save_parsed(pid, {
        "title": "实验研究", "paragraphs": paragraphs, "sections": [],
        "abstract": "", "keywords": [], "tables": [],
    })
    store.save_version(pid, paragraphs)
    store.save_requirement(pid, {
        "confirmed": True, "level": "undergraduate", "format": {},
        "deai": {"strength": "medium", "keep": [], "banned_phrases": []},
    })
    return pid


def test_qa_returns_only_the_model_cited_subset(paper, monkeypatch):
    llm = Replies({"answer": "实验样本量为 100 条。[p_0001]", "basis": ["p_0001"]})
    monkeypatch.setattr(api_paper, "_create_llm", lambda: llm)
    response = TestClient(server.app).post(
        f"/api/paper/{paper}/ask", json={"question": "实验样本量和对照组是什么？"},
    )
    assert response.status_code == 200, response.text
    # Both paragraphs really reached the model; only one was cited in its answer.
    context = llm.calls[0][-1]["content"]
    assert "p_0001" in context and "p_0002" in context
    data = response.json()
    assert data["basis"] == ["p_0001"]
    assert [item["id"] for item in data["evidence"]] == ["p_0001"]


def test_qa_invalid_source_is_retried_before_returning_verified_basis(paper, monkeypatch):
    llm = Replies(
        {"answer": "模型给出了不存在的来源。", "basis": ["p_9999"]},
        {"answer": "实验对照组采用基线模型。[p_0002]", "basis": ["p_0002"]},
    )
    monkeypatch.setattr(api_paper, "_create_llm", lambda: llm)
    response = TestClient(server.app).post(
        f"/api/paper/{paper}/ask", json={"question": "实验样本量和对照组是什么？"},
    )
    assert response.status_code == 200, response.text
    assert len(llm.calls) == 2
    assert response.json()["basis"] == ["p_0002"]
    assert [item["id"] for item in response.json()["evidence"]] == ["p_0002"]


def test_qa_without_citations_does_not_promote_retrieved_candidates(paper, monkeypatch):
    llm = Replies({"answer": "论文中未提及所需的实验细节。", "basis": []})
    monkeypatch.setattr(api_paper, "_create_llm", lambda: llm)
    response = TestClient(server.app).post(
        f"/api/paper/{paper}/ask", json={"question": "实验样本量和对照组是什么？"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["basis"] == []
    assert response.json()["evidence"] == []


def test_diagnosis_retries_invalid_issue_page_inside_model_validation(paper):
    llm = Replies(
        {"score": 80, "summary": "方法清晰。", "issues": [
            {"issue": "补充实验细节", "page": "unknown", "severity": "medium"},
        ]},
        {"score": 80, "summary": "方法清晰。", "issues": [
            {"issue": "补充实验细节", "page": 1, "severity": "medium"},
        ]},
    )
    result = analysis.diagnose(paper, llm, dimensions=["level"])
    assert len(llm.calls) == 2
    assert result["errors"] == {}
    assert result["dimensions"]["level"]["issues"][0]["page"] == 1
    assert store.load_analysis(paper, "level")["score"] == 80


def test_restore_rejects_nonobject_paragraphs_even_when_zip_hashes_match(paper, tmp_path):
    source_path = tmp_path / "valid.zip"
    backup.create_backup_archive(paper, str(source_path))
    with zipfile.ZipFile(source_path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    parsed_path = "paper/parsed.json"
    parsed = json.loads(entries[parsed_path])
    parsed["paragraphs"] = [7]
    entries[parsed_path] = json.dumps(parsed).encode("utf-8")
    manifest = json.loads(entries["manifest.json"])
    record = next(item for item in manifest["files"] if item["path"] == parsed_path)
    record["size"] = len(entries[parsed_path])
    record["sha256"] = hashlib.sha256(entries[parsed_path]).hexdigest()
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in entries.items():
            target.writestr(name, data)

    response = TestClient(server.app).post("/api/paper/restore", files={
        "file": ("invalid-structure.zip", payload.getvalue(), "application/zip"),
    })

    assert response.status_code == 400, response.text
    assert {item.name for item in Path(store.root_dir()).glob("p_*")} == {paper}
    assert not list(Path(store.root_dir()).glob(".restore-*"))
