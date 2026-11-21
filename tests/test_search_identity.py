"""Similarity caches belong to a query identity and a sufficient result budget."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from config.settings import settings
from paper import similar, store


@pytest.fixture
def search_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAPER_DATA_DIR", str(tmp_path / "papers"))
    paper_id = store.new_paper_id()
    store.save_meta(paper_id, {"title": "Software testing research", "filename": "test.pdf"})
    calls = []

    def search(self, term, limit):
        calls.append((term, limit))
        return [{"title": f"Software testing methods {index}", "doi": f"10.0000/test-{index}",
                 "abstract": "Software testing study with baseline comparisons.", "year": 2025}
                for index in range(limit)]

    monkeypatch.setattr(similar.AcademicSearchTool, "_search_crossref", search)
    monkeypatch.setattr(similar.AcademicSearchTool, "_search_openalex", search)
    return SimpleNamespace(paper_id=paper_id, calls=calls,
                           parsed={"title": "Software testing research", "keywords": ["testing", "coverage"]})


def test_a_one_result_cache_cannot_satisfy_a_twenty_result_request(search_env):
    env = search_env
    first = similar.find_similar(env.parsed, limit=1, paper_id=env.paper_id)
    assert len(first["items"]) == 1
    before = len(env.calls)

    result = similar.find_similar(env.parsed, limit=20, paper_id=env.paper_id)

    assert len(result["items"]) == 20
    assert result["cached"] is False
    assert len(env.calls) > before


@pytest.mark.parametrize("changed", ["title", "keywords"])
def test_changed_search_input_invalidates_recent_cache(search_env, changed):
    env = search_env
    similar.find_similar(env.parsed, limit=5, paper_id=env.paper_id)
    before = len(env.calls)
    parsed = deepcopy(env.parsed)
    parsed[changed] = "Weather forecasting methods" if changed == "title" else ["forecasting", "rainfall"]

    result = similar.find_similar(parsed, limit=5, paper_id=env.paper_id)

    assert result["cached"] is False
    assert len(env.calls) > before
    terms = [term for term, _ in env.calls[before:]]
    assert ("Weather forecasting methods" if changed == "title" else "forecasting rainfall") in terms


def test_a_larger_compatible_cache_is_trimmed_without_searching_again(search_env):
    env = search_env
    first = similar.find_similar(env.parsed, limit=20, paper_id=env.paper_id)
    assert len(first["items"]) == 20
    before = len(env.calls)

    result = similar.find_similar(env.parsed, limit=1, paper_id=env.paper_id)

    assert result["cached"] is True
    assert len(result["items"]) == 1
    assert result["items"] == first["items"][:1]
    assert len(env.calls) == before
    assert len(store.load_similar(env.paper_id)["items"]) == 20


def test_matching_search_identity_and_capacity_reuse_recent_cache(search_env):
    env = search_env
    first = similar.find_similar(env.parsed, limit=5, paper_id=env.paper_id)
    before = len(env.calls)

    result = similar.find_similar(env.parsed, limit=5, paper_id=env.paper_id)

    assert result["cached"] is True
    assert result["items"] == first["items"]
    assert len(env.calls) == before


def test_legacy_cache_without_source_and_ranking_identity_must_be_refreshed(search_env):
    env = search_env
    store.save_similar(env.paper_id, {
        "updated_at": similar._now(), "queries": [env.parsed["title"], "testing coverage"],
        "items": [{"title": "Legacy cached result", "doi": "10.0000/legacy", "similarity": 1}],
        "cached": False, "cache_days": 7, "errors": [], "error": "",
    })

    result = similar.find_similar(env.parsed, limit=1, paper_id=env.paper_id)

    assert result["cached"] is False
    assert env.calls
    assert all(item["doi"] != "10.0000/legacy" for item in result["items"])
