"""存储层并发与恢复：版本号唯一、索引不丢条目、索引可重建。"""
import json
import os
import threading
import time

import pytest

from config.settings import settings
from paper import rewrite, store


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    yield tmp_path


def _paper(pid: str, title: str = "") -> str:
    store.save_meta(pid, {"filename": f"{pid}.pdf", "title": title or pid})
    return pid


def test_concurrent_version_allocation_is_unique(store_env, monkeypatch):
    pid = _paper(store.new_paper_id())
    store.save_version(pid, [{"id": "p_0001", "text": "上传"}], "上传解析")
    original_list_versions = store.list_versions

    def slow_list_versions(paper_id):
        result = original_list_versions(paper_id)
        time.sleep(0.1)
        return result

    monkeypatch.setattr(store, "list_versions", slow_list_versions)
    start = threading.Barrier(2)
    results = []

    def worker():
        start.wait()
        results.append(store.save_version(pid, [{"id": "p_0001", "text": "x"}], "并发"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [1, 2]
    assert os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v1.json"))
    assert os.path.exists(os.path.join(store.paper_dir(pid), "versions", "v2.json"))


def test_concurrent_apply_keeps_both_paragraph_changes(store_env, monkeypatch):
    pid = _paper(store.new_paper_id(), "并发采纳")
    paragraphs = [
        {"id": "p_0001", "type": "body", "text": "第一段原文"},
        {"id": "p_0002", "type": "body", "text": "第二段原文"},
    ]
    store.save_version(pid, paragraphs, "初始")

    original_list_versions = store.list_versions

    def slow_list_versions(paper_id):
        result = original_list_versions(paper_id)
        time.sleep(0.1)
        return result

    monkeypatch.setattr(store, "list_versions", slow_list_versions)
    start = threading.Barrier(2)
    errors = []

    def worker(pid_body, text):
        try:
            start.wait()
            rewrite.apply_rewrite(pid, pid_body, text)
        except Exception as e:  # pragma: no cover - 断言会给出具体错误
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=("p_0001", "第一段已优化")),
        threading.Thread(target=worker, args=("p_0002", "第二段已优化")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert store.current_version(pid) == 2
    final = {p["id"]: p["text"] for p in store.current_paragraphs(pid)}
    assert final == {"p_0001": "第一段已优化", "p_0002": "第二段已优化"}


def test_concurrent_index_upserts_do_not_lose_entries(store_env, monkeypatch):
    ids = [store.new_paper_id() for _ in range(8)]
    for pid in ids:
        store.create_paper_dir(pid)
        store._write_json(os.path.join(store.paper_dir(pid), "meta.json"), {
            "id": pid,
            "filename": f"{pid}.pdf",
            "title": pid,
            "updated_at": "2026-01-01 00:00:00",
        })
    store._write_json(os.path.join(store.root_dir(), "index.json"), [])

    original_load_index = store._load_index

    def slow_load_index():
        data = original_load_index()
        time.sleep(0.15)
        return data

    monkeypatch.setattr(store, "_load_index", slow_load_index)
    start = threading.Barrier(len(ids))

    def worker(pid):
        start.wait()
        store._upsert_index(store.load_meta(pid))

    threads = [threading.Thread(target=worker, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(os.path.join(store.root_dir(), "index.json"), encoding="utf-8") as f:
        assert {m["id"] for m in json.load(f)} == set(ids)
    assert {p["id"] for p in store.list_papers()} == set(ids)


def test_concurrent_writes_to_same_meta_leave_valid_json(store_env):
    pid = _paper(store.new_paper_id(), "初始标题")
    titles = [f"标题 {i}" for i in range(20)]
    start = threading.Barrier(len(titles))

    def worker(title):
        start.wait()
        store.save_meta(pid, {"filename": "paper.pdf", "title": title})

    threads = [threading.Thread(target=worker, args=(title,)) for title in titles]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(os.path.join(store.paper_dir(pid), "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["title"] in titles
    assert [p["id"] for p in store.list_papers()] == [pid]


def test_index_rebuilds_after_missing_file(store_env):
    ids = {_paper(store.new_paper_id()) for _ in range(2)}
    index_path = os.path.join(store.root_dir(), "index.json")
    os.remove(index_path)

    listed = {p["id"] for p in store.list_papers()}

    assert listed == ids
    assert os.path.exists(index_path)


def test_index_rebuilds_after_corruption_and_divergence(store_env):
    pids = [_paper(store.new_paper_id()) for _ in range(2)]
    index_path = os.path.join(store.root_dir(), "index.json")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert {p["id"] for p in store.list_papers()} == set(pids)

    # 合法但遗漏条目的索引：与目录不一致时自动重建
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump([], f)
    assert {p["id"] for p in store.list_papers()} == set(pids)

    # 指向已删除目录的陈旧条目应被清理
    stale = {"id": store.new_paper_id(), "updated_at": "2099-01-01 00:00:00"}
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump([stale], f)
    assert {p["id"] for p in store.list_papers()} == set(pids)
    with open(index_path, encoding="utf-8") as f:
        assert {m["id"] for m in json.load(f)} == set(pids)
