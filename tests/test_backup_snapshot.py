"""Backups must contain one restorable state while normal writes continue."""
import threading
from pathlib import Path

import pytest

from config.settings import settings
from paper import backup, store


@pytest.fixture(autouse=True)
def isolated_papers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))


def _paper(version_count=1):
    paper_id = store.new_paper_id()
    root = Path(store.create_paper_dir(paper_id))
    (root / "original.pdf").write_bytes(b"%PDF-1.4\n% backup fixture\n")
    store.save_meta(paper_id, {
        "filename": "snapshot.pdf", "source_type": "pdf",
        "title": "Snapshot paper", "current_version": 0,
    })
    store.save_parsed(paper_id, {
        "paragraphs": [{"id": "p_0001", "type": "body", "text": "version 0"}],
        "sections": [],
    })
    for number in range(version_count):
        store.save_version(paper_id, [{
            "id": "p_0001", "type": "body", "text": f"version {number}",
        }])
    return paper_id


@pytest.mark.parametrize("operation", ["save", "compact", "delete"])
def test_backup_remains_restorable_during_storage_changes(
        tmp_path, monkeypatch, operation):
    paper_id = _paper(version_count=5)
    destination = tmp_path / "snapshot.zip"
    enumerate_files = backup._backup_files
    attempted = threading.Event()
    errors = []
    threads = []

    def mutate():
        def write():
            if operation == "save":
                store.save_version(paper_id, [{
                    "id": "p_0001", "type": "body", "text": "version 5",
                }])
            elif operation == "compact":
                store.compact_versions(paper_id, keep_recent=1)
            else:
                store.delete_paper(paper_id)

        # If the snapshot already holds the public storage lock, let its reader
        # proceed, then perform the write after that transaction finishes. With
        # no snapshot lock, finish the mutation before the reader can continue.
        lock = store.locked()
        acquired = lock.acquire(blocking=False)
        try:
            if acquired:
                write()
                attempted.set()
            else:
                attempted.set()
                with lock:
                    write()
        except Exception as error:
            errors.append(error)
        finally:
            if acquired:
                lock.release()
            attempted.set()

    def enumerate_then_mutate(current_id):
        files = enumerate_files(current_id)
        if not threads:
            worker = threading.Thread(target=mutate, daemon=True)
            threads.append(worker)
            worker.start()
            assert attempted.wait(5), "storage mutation did not start"
        return files

    monkeypatch.setattr(backup, "_backup_files", enumerate_then_mutate)
    try:
        backup.create_backup_archive(paper_id, str(destination))
    finally:
        for worker in threads:
            worker.join(5)
        assert all(not worker.is_alive() for worker in threads)
        assert not errors

    with destination.open("rb") as archive:
        restored = backup.restore_backup_archive(archive)
    restored_id = restored["paper_id"]
    current = restored["current_version"]
    versions = store.list_versions(restored_id)
    assert {entry["version"] for entry in versions} == set(range(current + 1))
    assert store.current_paragraphs(restored_id)[0]["text"] == f"version {current}"
    assert store.load_version(restored_id, 0)["paragraphs"][0]["text"] == "version 0"


def test_backup_generation_rejects_more_files_than_restore_accepts(tmp_path, monkeypatch):
    paper_id = _paper(version_count=3)
    destination = tmp_path / "too-many-files.zip"
    # Three required files plus three versions exceed this small test limit.
    monkeypatch.setattr(backup, "MAX_BACKUP_FILES", 5)

    with pytest.raises(backup.BackupError, match="数量"):
        backup.create_backup_archive(paper_id, str(destination))

    assert not destination.exists()
