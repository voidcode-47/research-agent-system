# -*- coding: utf-8 -*-
"""论文列表搜索、分页与索引维护。"""
import pytest
from fastapi.testclient import TestClient

import server
from config.settings import settings
from paper import store
from utils import quota

client = TestClient(server.app)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    yield


def _make_paper(title, filename="paper.pdf"):
    pid = store.new_paper_id()
    store.save_meta(pid, {
        "filename": filename,
        "title": title,
        "pages": 1,
        "chars": 10,
        "paragraph_count": 1,
        "status": "uploaded",
        "current_version": 0,
    })
    return pid


def test_search_filters_title_filename_and_id_case_insensitively(env):
    nn1 = _make_paper("神经网络在图像识别中的应用", "cnn.pdf")
    nn2 = _make_paper("神经网络综述", "review.pdf")
    other = _make_paper("实验结果", "Result-Final.PDF")

    result = store.search_papers("神经", 0, 50)
    assert result["total"] == 2
    assert {p["id"] for p in result["papers"]} == {nn1, nn2}

    assert store.search_papers("result-final", 0, 50)["total"] == 1
    assert store.search_papers(other, 0, 50)["total"] == 1
    assert store.search_papers("", 0, 50)["total"] == 3


def test_pagination_reports_total_offset_and_has_more(env):
    ids = [_make_paper(f"论文 {i}", f"p{i}.pdf") for i in range(5)]

    first = store.search_papers("", 0, 2)
    assert first["total"] == 5
    assert first["offset"] == 0
    assert first["limit"] == 2
    assert first["has_more"] is True
    assert len(first["papers"]) == 2

    last = store.search_papers("", 4, 2)
    assert last["total"] == 5
    assert last["offset"] == 4
    assert last["has_more"] is False
    assert len(last["papers"]) == 1
    assert last["papers"][0]["id"] in ids


def test_list_api_returns_pagination_metadata_and_validates_parameters(env):
    _make_paper("神经网络 A")
    _make_paper("神经网络 B")
    _make_paper("其他论文")

    response = client.get("/api/paper", params={"q": "神经", "limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["has_more"] is True
    assert len(body["papers"]) == 1

    second = client.get(
        "/api/paper", params={"q": "神经", "offset": 1, "limit": 1}
    ).json()
    assert second["has_more"] is False
    assert len(second["papers"]) == 1

    assert client.get("/api/paper", params={"offset": -1}).status_code == 422
    assert client.get("/api/paper", params={"limit": 0}).status_code == 422
    assert client.get("/api/paper", params={"limit": 201}).status_code == 422
    assert client.get("/api/paper", params={"q": "x" * 201}).status_code == 422


def test_valid_index_avoids_full_directory_rescan(env, monkeypatch):
    pid = _make_paper("增量索引测试")

    def fail_scan():
        raise AssertionError("list/search should not rescan paper directories")

    monkeypatch.setattr(store, "_scan_papers", fail_scan)

    assert store.search_papers("增量", 0, 10)["total"] == 1
    assert [p["id"] for p in store.list_papers()] == [pid]
