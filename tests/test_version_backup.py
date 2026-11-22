"""版本压缩归档、备份与恢复。"""
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.paper as api_paper
import server
from config.settings import settings
from paper import backup, store
from utils import quota

client = TestClient(server.app)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(settings, "DAILY_QUOTA", 100)
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    api_paper.PAPER_TASKS.clear()
    yield
    api_paper.PAPER_TASKS.clear()


def _make_paper(version_count=7, note_prefix="v"):
    pid = store.new_paper_id()
    paper_dir = store.create_paper_dir(pid)
    with open(os.path.join(paper_dir, "original.pdf"), "wb") as f:
        f.write(b"%PDF-1.4\n% synthetic backup fixture\n")
    store.save_meta(pid, {
        "id": pid,
        "filename": "sample.pdf",
        "title": "备份测试论文",
        "pages": 1,
        "chars": 20,
        "paragraph_count": 1,
        "source_type": "pdf",
        "status": "uploaded",
        "current_version": 0,
    })
    store.save_parsed(pid, {
        "title": "备份测试论文",
        "text_chars": 20,
        "paragraphs": [{"id": "p_0001", "type": "body", "text": "v0 正文"}],
        "sections": [],
    })
    store.save_requirement(pid, {
        "level": "undergraduate",
        "format": {"template": "generic_thesis", "reference_style": "gbt7714"},
        "deai": {"strength": "medium", "keep": [], "banned_phrases": []},
        "notes": "backup",
        "confirmed": True,
    })
    for i in range(version_count):
        store.save_version(
            pid,
            [{"id": "p_0001", "type": "body", "text": f"{note_prefix}{i} 正文"}],
            note=f"{note_prefix}{i}",
        )
    store.save_analysis(pid, "level", {"score": 81, "summary": "结构完整", "stale": False})
    store.save_similar(pid, {"items": [], "cached_at": "2026-01-01 00:00:00"})
    store.save_rewrite_guide(pid, {"terminology": [], "version": 0})
    return pid


def _backup_bytes(pid):
    response = client.get(f"/api/paper/{pid}/backup")
    assert response.status_code == 200, response.text
    return response.content


def _restore(data, name="backup.zip"):
    return client.post(
        "/api/paper/restore",
        files={"file": (name, data, "application/zip")},
    )


def _rewrite_zip(data, replacements=None, extras=None, manifest_patch=None):
    replacements = replacements or {}
    extras = extras or {}
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            payload = src.read(info)
            if info.filename == "manifest.json" and manifest_patch:
                parsed = json.loads(payload.decode("utf-8"))
                manifest_patch(parsed)
                payload = json.dumps(parsed, ensure_ascii=False).encode("utf-8")
            payload = replacements.get(info.filename, payload)
            dst.writestr(info, payload)
        for name, payload in extras.items():
            dst.writestr(name, payload)
    return out.getvalue()


def test_compact_versions_archives_old_and_keeps_v0_current(env):
    pid = _make_paper()
    result = store.compact_versions(pid, keep_recent=2)

    assert result["archived_versions"] == [1, 2, 3, 4]
    assert result["kept_versions"] == [0, 5, 6]
    assert result["current_version"] == 6
    assert result["loose_count"] == 3
    assert result["archived_count"] == 4
    assert result["version_count"] == 7

    assert os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v0.json"))
    assert os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v6.json"))
    assert not os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v1.json"))
    assert store.load_version(pid, 1)["paragraphs"][0]["text"] == "v1 正文"
    assert store.load_version(pid, 5)["paragraphs"][0]["text"] == "v5 正文"

    listed = {v["version"]: v for v in store.list_versions(pid)}
    assert listed[1]["archived"] is True
    assert listed[6]["archived"] is False
    assert store.save_version(pid, [{"id": "p_0001", "text": "next"}], "after") == 7


def test_compact_versions_keeps_current_even_when_older(env):
    pid = _make_paper()
    meta = store.load_meta(pid)
    meta["current_version"] = 2
    store.save_meta(pid, meta)

    result = store.compact_versions(pid, keep_recent=1)

    assert result["kept_versions"] == [0, 2, 6]
    assert result["archived_versions"] == [1, 3, 4, 5]
    assert store.load_version(pid, 3)["paragraphs"][0]["text"] == "v3 正文"


def test_compact_versions_noop_and_zero_keep(env):
    pid = _make_paper(version_count=3)
    result = store.compact_versions(pid, keep_recent=5)
    assert result["archived_versions"] == []

    result = store.compact_versions(pid, keep_recent=0)
    assert result["archived_versions"] == [1]
    assert result["kept_versions"] == [0, 2]


def test_compact_versions_rejects_corrupt_archive(env):
    pid = _make_paper()
    archive = os.path.join(store.paper_dir(pid), "versions", "archive.json.gz")
    with open(archive, "wb") as f:
        f.write(b"not a gzip stream")
    with pytest.raises(ValueError, match="损坏"):
        store.compact_versions(pid, keep_recent=0)
    assert os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v1.json"))


def test_cleanup_api_reports_archived_versions(env):
    pid = _make_paper()
    response = client.post(
        f"/api/paper/{pid}/versions/cleanup", json={"keep_recent": 2}
    )
    assert response.status_code == 200, response.text
    assert response.json()["archived_versions"] == [1, 2, 3, 4]

    listed = client.get(f"/api/paper/{pid}/versions").json()
    assert listed["version_count"] == 7
    assert listed["archived_count"] == 4
    assert client.get(f"/api/paper/{pid}/version/2").json()["paragraphs"][0]["text"] == "v2 正文"
    detail = client.get(f"/api/paper/{pid}").json()
    assert detail["archived_count"] == 4

    bad = client.post(f"/api/paper/{pid}/versions/cleanup", json={"keep_recent": -1})
    assert bad.status_code == 422


def test_backup_contains_manifest_and_known_files(env):
    pid = _make_paper()
    data = _backup_bytes(pid)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert manifest["app"] == backup.BACKUP_APP
    assert manifest["format"] == backup.BACKUP_FORMAT
    assert manifest["paper_id"] == pid
    assert manifest["file_count"] == len(manifest["files"])
    assert "paper/meta.json" in names
    assert "paper/parsed.json" in names
    assert "paper/original.pdf" in names
    assert "paper/versions/v6.json" in names
    assert "paper/analysis/level.json" in names
    assert "paper/requirement.json" in names
    assert not any(name.startswith("paper/exports/") for name in names)


def test_backup_restore_creates_new_paper_and_preserves_archive(env):
    pid = _make_paper()
    store.compact_versions(pid, keep_recent=2)
    before = {
        "meta": store.load_meta(pid),
        "version": store.current_version(pid),
        "paragraphs": store.current_paragraphs(pid),
        "requirement": store.load_requirement(pid),
        "analysis": store.load_analysis(pid, "level"),
        "similar": store.load_similar(pid),
        "original": Path(store.paper_dir(pid), "original.pdf").read_bytes(),
        "archived_v2": store.load_version(pid, 2),
    }

    response = _restore(_backup_bytes(pid))
    assert response.status_code == 200, response.text
    result = response.json()
    new_pid = result["paper_id"]
    assert new_pid != pid
    assert result["restored_from"] == pid
    assert result["current_version"] == 6
    assert result["archived_count"] == 4

    assert store.load_meta(new_pid)["restored_from"] == pid
    assert store.current_version(new_pid) == before["version"]
    assert store.current_paragraphs(new_pid) == before["paragraphs"]
    assert store.load_requirement(new_pid) == before["requirement"]
    assert store.load_analysis(new_pid, "level")["score"] == before["analysis"]["score"]
    assert store.load_similar(new_pid) == before["similar"]
    assert store.load_version(new_pid, 2) == before["archived_v2"]
    assert Path(store.paper_dir(new_pid), "original.pdf").read_bytes() \
        == before["original"]

    # 原论文保持不变，恢复副本出现在列表中
    assert store.current_version(pid) == 6
    ids = {p["id"] for p in store.list_papers()}
    assert {pid, new_pid} <= ids


def test_restore_rejects_missing_manifest(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("paper/meta.json", "{}")
    response = _restore(buf.getvalue())
    assert response.status_code == 400
    assert "manifest" in response.json()["detail"]


def test_restore_rejects_path_traversal(env):
    pid = _make_paper()
    data = _backup_bytes(pid)
    bad = _rewrite_zip(
        data,
        extras={"paper/../evil.txt": b"x"},
        manifest_patch=lambda m: m["files"].append({
            "path": "paper/../evil.txt",
            "size": 1,
            "sha256": "0" * 64,
        }),
    )
    response = _restore(bad)
    assert response.status_code == 400
    assert "不允许的路径" in response.json()["detail"]


def test_restore_rejects_hash_mismatch_and_leaves_no_paper(env):
    pid = _make_paper()
    data = _backup_bytes(pid)
    tampered = _rewrite_zip(
        data, replacements={"paper/meta.json": b'{"id":"tampered"}'}
    )
    before = {p["id"] for p in store.list_papers()}
    response = _restore(tampered)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "校验失败" in detail or "大小不一致" in detail
    assert {p["id"] for p in store.list_papers()} == before
    assert not any(
        name.startswith(".restore-")
        for name in os.listdir(store.root_dir())
    )


def test_restore_rejects_unlisted_file(env):
    pid = _make_paper()
    data = _backup_bytes(pid)
    bad = _rewrite_zip(data, extras={"paper/evil.txt": b"x"})
    response = _restore(bad)
    assert response.status_code == 400
    assert "清单外文件" in response.json()["detail"]


def test_restore_rejects_unsupported_format(env):
    pid = _make_paper()
    data = _backup_bytes(pid)
    bad = _rewrite_zip(
        data, manifest_patch=lambda m: m.update({"app": "other-app"})
    )
    response = _restore(bad)
    assert response.status_code == 400
    assert "备份格式" in response.json()["detail"]


def test_restore_rejects_non_zip(env):
    response = _restore(b"not a zip", name="backup.zip")
    assert response.status_code == 400
    assert "zip" in response.json()["detail"]


def test_restore_rejects_uncompressed_limit(env, monkeypatch):
    pid = _make_paper()
    data = _backup_bytes(pid)
    monkeypatch.setattr(backup, "MAX_BACKUP_UNCOMPRESSED_BYTES", 100)
    response = _restore(data)
    assert response.status_code == 400
    assert "上限" in response.json()["detail"]
