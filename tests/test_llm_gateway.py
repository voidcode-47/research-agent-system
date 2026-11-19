# -*- coding: utf-8 -*-
"""LLM 网关：任务白名单 / JSON 提取 / 重试 / 模型调用失败直报。"""
import pytest

from config.settings import settings
from llm import gateway
from llm.gateway import LLMGateway, extract_json
from utils import quota


class FakeResp:
    def __init__(self, content):
        self.content = content
        self.usage = {"total_tokens": 7}


class FakeLLM:
    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = 0

    def chat(self, messages, temperature=0.7, max_tokens=None):
        self.calls += 1
        return FakeResp(self.contents.pop(0))


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
