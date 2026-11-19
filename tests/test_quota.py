# -*- coding: utf-8 -*-
"""按日配额：消耗 / 拒绝 / 退还 / 清理 / 快照。"""
import json

import pytest

from config.settings import settings
from utils import quota


@pytest.fixture(autouse=True)
def clean_reservations():
    quota._reservations.clear()
    yield
    quota._reservations.clear()


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


def test_preflight_passes_when_quota_is_sufficient(q):
    quota.consume()
    quota.preflight(2)
    quota.preflight(2, key="other")


def test_preflight_rejects_insufficient_quota(q):
    quota.consume(amount=2)
    with pytest.raises(quota.QuotaExceeded) as ei:
        quota.preflight(2)
    msg = str(ei.value)
    assert "需要 2 次模型调用" in msg
    assert "仅剩 1 次" in msg


def test_preflight_does_not_consume(q):
    quota.preflight(3)
    assert quota.usage_snapshot()["used"] == 0


def test_preflight_admin_and_nonpositive_amount_bypass(q):
    quota.consume(amount=3)
    quota.preflight(5, unlimited=True)
    quota.preflight(0)
    quota.preflight(-1)


def test_reserve_reduces_remaining_without_consuming(q):
    token = quota.reserve(2)
    snap = quota.usage_snapshot()
    assert snap["used"] == 0
    assert snap["reserved"] == 2
    assert snap["remaining"] == 1

    quota.release(token)
    snap = quota.usage_snapshot()
    assert snap["reserved"] == 0
    assert snap["remaining"] == 3


def test_reserve_rejects_when_capacity_is_held_by_other_tasks(q):
    quota.reserve(2)
    with pytest.raises(quota.QuotaExceeded) as ei:
        quota.reserve(2)
    assert "需要 2 次模型调用" in str(ei.value)
    assert "仅剩 1 次" in str(ei.value)
    assert quota.usage_snapshot()["used"] == 0


def test_reserve_bypasses_admin_zero_and_negative_amounts(q):
    assert quota.reserve(5, unlimited=True) is None
    assert quota.reserve(0) is None
    assert quota.reserve(-1) is None
    assert quota.usage_snapshot()["reserved"] == 0


def test_consume_and_refund_draw_from_reservation(q):
    token = quota.reserve(2)
    ctx = quota.set_reservation(token)
    try:
        quota.consume()
        snap = quota.usage_snapshot()
        assert snap["used"] == 1
        assert snap["reserved"] == 1
        assert snap["remaining"] == 1

        quota.refund()
        snap = quota.usage_snapshot()
        assert snap["used"] == 0
        assert snap["reserved"] == 2
    finally:
        quota.reset_reservation(ctx)


def test_consume_rejects_exceeding_reserved_amount(q):
    token = quota.reserve(1)
    ctx = quota.set_reservation(token)
    try:
        with pytest.raises(quota.QuotaExceeded) as ei:
            quota.consume(amount=2)
        assert "预留额度不足" in str(ei.value)
    finally:
        quota.reset_reservation(ctx)


def test_reservations_count_against_check_and_preflight(q):
    token = quota.reserve(3)
    with pytest.raises(quota.QuotaExceeded):
        quota.check()
    with pytest.raises(quota.QuotaExceeded):
        quota.preflight(1)
    quota.release(token)
    quota.check()
    quota.preflight(3)
