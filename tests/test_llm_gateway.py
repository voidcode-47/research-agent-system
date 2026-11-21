# -*- coding: utf-8 -*-
"""LLM 网关：任务白名单 / JSON 提取 / 重试 / 模型调用失败直报。"""
import pytest

from config.settings import settings
from llm.gateway import LLMGateway, extract_json
from utils import auth, quota


class FakeResp:
    def __init__(self, content, finish_reason=""):
        self.content = content
        self.usage = {"total_tokens": 7}
        self.finish_reason = finish_reason


class FakeLLM:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = 0

    def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls += 1
        item = self.contents.pop(0)
        return item if isinstance(item, FakeResp) else FakeResp(item)


class RaisingLLM:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls += 1
        raise self.exc


def _used() -> int:
    return quota.usage_snapshot()["used"]


@pytest.fixture(autouse=True)
def _quota(tmp_path, monkeypatch):
    monkeypatch.setattr(quota, "QUOTA_PATH", str(tmp_path / "quota.json"))
    monkeypatch.setattr(settings, "DAILY_QUOTA", 50)


def test_unknown_task_type_rejected():
    llm = FakeLLM()
    with pytest.raises(ValueError, match="不允许的 LLM 任务"):
        LLMGateway(llm).run("free_chat", [{"role": "user", "content": "hi"}])


def test_extract_json_handles_fence_and_noise():
    assert extract_json('说明\n```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('前缀 {"a": {"b": "}"}} 后缀')["a"]["b"] == "}"
    assert extract_json("没有 JSON") is None


def test_invalid_json_retries_once_then_fails():
    llm = FakeLLM("不是 JSON", "还不是")
    with pytest.raises(RuntimeError, match="模型输出不是合法 JSON"):
        LLMGateway(llm).run(
            "deai_rewrite", [{"role": "user", "content": "x"}],
            validator=lambda d: isinstance(d, dict),
        )
    assert llm.calls == 2


def test_validator_failure_retries_with_hint():
    llm = FakeLLM('{"score": 120, "summary": "x"}', '{"score": 80, "summary": "x"}')
    data = LLMGateway(llm).run(
        "level_diagnose", [{"role": "user", "content": "x"}],
        validator=lambda d: 0 <= float(d.get("score", -1)) <= 100,
    )
    assert data["score"] == 80
    assert llm.calls == 2


def test_provider_error_not_retried():
    llm = FakeLLM("API 调用失败: Error code: 401 - Authentication Fails")
    with pytest.raises(RuntimeError, match="401.*添加模型"):
        LLMGateway(llm).run("feasibility", [{"role": "user", "content": "x"}])
    assert llm.calls == 1


def test_rate_limit_notice_not_retried():
    llm = FakeLLM("⚠️ API 限流（请求过于频繁）：429")
    with pytest.raises(RuntimeError, match="限流"):
        LLMGateway(llm).run("paper_qa", [{"role": "user", "content": "x"}])
    assert llm.calls == 1


def test_successful_call_charges_once():
    llm = FakeLLM('{"ok": true}')
    LLMGateway(llm).run(
        "feasibility", [{"role": "user", "content": "x"}],
        validator=lambda d: d.get("ok") is True,
    )
    assert _used() == 1


def test_retry_within_one_call_charges_once():
    llm = FakeLLM("不是 JSON", '{"ok": true}')
    LLMGateway(llm).run(
        "feasibility", [{"role": "user", "content": "x"}],
        validator=lambda d: d.get("ok") is True,
    )
    assert llm.calls == 2
    assert _used() == 1


def test_invalid_output_keeps_one_charge():
    llm = FakeLLM("不是 JSON", "还不是")
    with pytest.raises(RuntimeError, match="模型输出不是合法 JSON"):
        LLMGateway(llm).run(
            "deai_rewrite", [{"role": "user", "content": "x"}],
            validator=lambda d: isinstance(d, dict),
        )
    assert llm.calls == 2
    assert _used() == 1


def test_provider_error_refunds_charge():
    llm = FakeLLM("API 调用失败: Error code: 401 - Authentication Fails")
    with pytest.raises(RuntimeError, match="401.*添加模型"):
        LLMGateway(llm).run("feasibility", [{"role": "user", "content": "x"}])
    assert llm.calls == 1
    assert _used() == 0


def test_transport_failure_refunds_charge():
    llm = RaisingLLM(ConnectionError("offline"))
    with pytest.raises(RuntimeError, match="执行失败"):
        LLMGateway(llm).run("paper_qa", [{"role": "user", "content": "x"}])
    assert llm.calls == 2
    assert _used() == 0


def test_late_provider_error_keeps_charge_after_real_output():
    llm = FakeLLM("不是 JSON", "API 调用失败: 401")
    with pytest.raises(RuntimeError, match="401"):
        LLMGateway(llm).run(
            "deai_rewrite", [{"role": "user", "content": "x"}],
            validator=lambda d: isinstance(d, dict),
        )
    assert llm.calls == 2
    assert _used() == 1


def test_exhausted_quota_blocks_before_model_call(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_QUOTA", 0)
    llm = FakeLLM('{"ok": true}')
    with pytest.raises(quota.QuotaExceeded):
        LLMGateway(llm).run("feasibility", [{"role": "user", "content": "x"}])
    assert llm.calls == 0
    assert _used() == 0


def test_admin_failure_does_not_refund_regular_quota():
    quota.consume()
    token = auth.set_admin(True)
    try:
        llm = FakeLLM("API 调用失败: 401")
        with pytest.raises(RuntimeError):
            LLMGateway(llm).run("feasibility", [{"role": "user", "content": "x"}])
    finally:
        auth.reset_admin(token)
    assert _used() == 1


def test_continuation_merge_drops_repeated_overlap():
    from llm.gateway import _merge_continuation

    assert _merge_continuation("abcdef", "defghi") == "abcdefghi"
    assert _merge_continuation("abc", "xyz") == "abc\n\nxyz"


def test_text_truncation_auto_continues_and_reports_progress():
    llm = FakeLLM(
        FakeResp("## 一、研究背景\n人脸识别发展迅速。", "length"),
        FakeResp("\n\n## 二、研究现状\n已有大量研究。", "stop"),
    )
    steps = []
    text = LLMGateway(llm).run(
        "research_report", [{"role": "user", "content": "x"}],
        expect_json=False,
        validator=lambda t: len(t) >= 10,
        on_progress=steps.append,
    )
    assert "## 一、研究背景" in text and "## 二、研究现状" in text
    assert steps == [1]
    assert llm.calls == 2
    assert _used() == 1


def test_text_truncation_keeps_partial_after_continuation_limit():
    llm = FakeLLM(
        FakeResp("A" * 100, "length"),
        FakeResp("B" * 100, "length"),
        FakeResp("C" * 100, "length"),
    )
    text = LLMGateway(llm).run(
        "research_report", [{"role": "user", "content": "x"}],
        expect_json=False,
        validator=lambda t: len(t) >= 50,
    )
    assert "A" * 100 in text and "B" * 100 in text and "C" * 100 in text
    assert llm.calls == 3


def test_json_truncation_still_retries_and_fails():
    llm = FakeLLM(
        FakeResp('{"score": 80', "length"),
        FakeResp('{"score": 80', "length"),
    )
    with pytest.raises(RuntimeError, match="未完整结束"):
        LLMGateway(llm).run(
            "level_diagnose", [{"role": "user", "content": "x"}],
            validator=lambda d: isinstance(d, dict),
        )
    assert llm.calls == 2
