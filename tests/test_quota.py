# -*- coding: utf-8 -*-
"""按日配额：消耗 / 拒绝 / 退还 / 清理 / 快照。"""
import json

import pytest

from config.settings import settings
from utils import quota


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(quota, "_today", lambda: "2026-01-02")
    monkeypatch.setattr(settings, "DAILY_QUOTA", 3)
    return tmp_path / "quota.json"


def test_consume_until_exhausted(q):
    assert quota.consume() == 1
    assert quota.consume() == 2
    assert quota.check() == 2
    assert quota.consume() == 3
    with pytest.raises(quota.QuotaExceeded) as ei:
        quota.consume()
    assert "配额已用完" in str(ei.value)
    with pytest.raises(quota.QuotaExceeded):
        quota.check()


def test_refund_never_negative(q):
    quota.consume()
    assert quota.refund() == 0
    assert quota.refund() == 0


def test_snapshot_fields(q):
    quota.consume()
    snap = quota.usage_snapshot()
    assert snap["date"] == "2026-01-02"
    assert snap["limit"] == 3
    assert snap["used"] == 1
    assert snap["remaining"] == 2
    assert snap["history"][-1] == {"date": "2026-01-02", "used": 1}


def test_amount_counts_multiple_calls(q):
    assert quota.consume(amount=2) == 2
    assert quota.check() == 2
    assert quota.usage_snapshot()["remaining"] == 1


def test_keys_are_isolated(q):
    quota.consume("a")
    quota.consume("a")
    assert quota.usage_snapshot("a")["used"] == 2
    assert quota.usage_snapshot("b")["used"] == 0
    assert quota.consume("b") == 1


def test_old_days_are_pruned(q):
    q.write_text(json.dumps({
        "2025-12-01": {"local": 9},
        "2026-01-01": {"local": 2},
        "2026-01-02": {"local": 0},
        "bad-date": {"local": 5},
    }), encoding="utf-8")
    quota.consume()
    data = json.loads(q.read_text(encoding="utf-8"))
    assert "2025-12-01" not in data
    assert "bad-date" not in data
    assert data["2026-01-01"]["local"] == 2
    assert data["2026-01-02"]["local"] == 1


def test_corrupt_file_is_ignored(q):
    q.write_text("{not json", encoding="utf-8")
    assert quota.consume() == 1
    assert quota.usage_snapshot()["used"] == 1
