# -*- coding: utf-8 -*-
"""缺陷修复回归测试。

覆盖本轮修复的问题点，防止回归：
- 向量相似度换算（Chroma 默认 L2 被当成余弦距离）
- .env 解码失败导致配置被清空
- token 统计漏掉 tool_calls / 中文被低估
- 硬切分块超过 chunk_size 上限
- 破坏性命令模式放过 rd /q /s、del/f
- 幻觉检测对中文恒返回 0
- 短期记忆压缩守卫永不触发
- token 预算守卫永不触发
- supervisor 寒暄误判 + 质检重复调用 LLM
- Base URL 规范化破坏带版本段的端点
"""
import pytest
from unittest.mock import MagicMock

from llm.factory import normalize_base_url
from memory.short_term import ShortTermMemory
from orchestration.supervisor import Supervisor
from rag.text_splitter import TextSplitter
from tools.vector_store import VectorStore
from utils.env_file import update_env, load_env_lines, get_env_value
from utils.safety import SafetyGuard
from utils.token_counter import count_messages_tokens, estimate_tokens


class TestVectorSimilarityConversion:
    """Chroma 默认空间是 l2（平方欧氏距离），不能直接按余弦距离换算。"""

    def test_cosine_space_is_complement(self):
        assert VectorStore._distance_to_score(0.0, "cosine") == 1.0
        assert VectorStore._distance_to_score(0.2, "cosine") == pytest.approx(0.8)
        assert VectorStore._distance_to_score(1.0, "cosine") == 0.0

    def test_l2_space_is_monotonic_and_never_collapses(self):
        # 旧实现 max(0, 1 - dist) 会让任何距离 >=1 的命中一律显示 0 分，
        # 导致检索阈值把本应命中的片段全部过滤掉
        assert VectorStore._distance_to_score(0.0, "l2") == 1.0
        assert VectorStore._distance_to_score(1.0, "l2") == pytest.approx(0.5)
        assert VectorStore._distance_to_score(3.0, "l2") > 0
        assert (
            VectorStore._distance_to_score(3.0, "l2")
            < VectorStore._distance_to_score(1.0, "l2")
        )

    def test_unknown_space_falls_back_to_l2(self):
        assert VectorStore._distance_to_score(1.0, None) == pytest.approx(0.5)

    def test_new_collection_uses_cosine_space(self, temp_vector_store):
        temp_vector_store.get_or_create_collection("kb")
        temp_vector_store._client.get_or_create_collection.assert_called_once_with(
            name="kb", metadata={"hnsw:space": "cosine"}
        )

    def test_search_uses_collection_space(self, temp_vector_store):
        coll = MagicMock()
        coll.metadata = {"hnsw:space": "cosine"}
        coll.count.return_value = 1
        coll.query.return_value = {
            "documents": [["片段"]],
            "metadatas": [[{"source": "a.pdf"}]],
            "distances": [[0.2]],
        }
        temp_vector_store._client.get_or_create_collection.return_value = coll
        temp_vector_store._embed_one = lambda text: [0.1, 0.2]

        hits = temp_vector_store.search("kb", "问题", k=1)

        assert hits[0]["score"] == pytest.approx(0.8)


class TestEnvFileRobustness:
    """update_env 绝不能因为解码问题把用户的 .env 清空。"""

    def test_gbk_file_is_read_and_preserved(self, tmp_path):
        p = tmp_path / ".env"
        p.write_bytes("OPENAI_API_KEY=sk-x\n# 中文注释\n".encode("gbk"))

        update_env({"ZHIPU_API_KEY": "z1"}, p)

        text = p.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-x" in text
        assert "ZHIPU_API_KEY=z1" in text

    def test_binary_junk_does_not_wipe_existing_keys(self, tmp_path):
        p = tmp_path / ".env"
        # 末尾夹带非法字节：整体解码不可靠，但已存在的 ASCII 配置项必须保住。
        # 注意不能拿它去试 UTF-16——偶数长度字节串会被"成功"解成乱码
        p.write_bytes(b"OPENAI_API_KEY=sk-x\n\x80\x81\x82\n")

        update_env({"ZHIPU_API_KEY": "z1"}, p)

        text = p.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-x" in text
        assert "ZHIPU_API_KEY=z1" in text

    def test_utf16_file_with_bom_is_decoded(self, tmp_path):
        p = tmp_path / ".env"
        p.write_bytes("OPENAI_API_KEY=sk-x\n".encode("utf-16"))  # 带 BOM

        assert get_env_value(load_env_lines(p), "OPENAI_API_KEY") == "sk-x"

    def test_key_with_spaces_is_updated_not_duplicated(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("OPENAI_API_KEY = old\n", encoding="utf-8")

        update_env({"OPENAI_API_KEY": "new"}, p)

        text = p.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=new" in text
        assert text.count("OPENAI_API_KEY") == 1

    def test_export_style_and_comments(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# 注释\nexport ZHIPU_API_KEY=old\n", encoding="utf-8")

        update_env({"ZHIPU_API_KEY": "new"}, p)

        text = p.read_text(encoding="utf-8")
        assert "# 注释" in text
        assert text.count("ZHIPU_API_KEY") == 1
        assert get_env_value(load_env_lines(p), "ZHIPU_API_KEY") == "new"


class TestTokenAccounting:
    def test_tool_calls_payload_is_counted(self):
        with_calls = count_messages_tokens([{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "大语言模型进展"}'},
            }],
        }])
        without = count_messages_tokens([{"role": "assistant", "content": None}])
        assert with_calls > without

    def test_chinese_estimate_is_not_severely_underestimated(self):
        text = "研究报告" * 100  # 400 个中文字符
        # 旧的 2.5 系数会估成 160；中文实际接近 267
        assert estimate_tokens(text) > 240
        assert estimate_tokens("") == 0


class TestTextSplitterHardSplit:
    def test_hard_split_respects_chunk_size(self):
        splitter = TextSplitter(chunk_size=800, chunk_overlap=100)
        chunks = splitter.split("研究报告" * 500)

        assert chunks
        assert max(len(c) for c in chunks) <= 800

    def test_separator_path_keeps_overlap(self):
        splitter = TextSplitter(chunk_size=50, chunk_overlap=10)
        chunks = splitter.split("句子。".join(f"第{i}段内容" for i in range(40)))

        assert len(chunks) > 1


class TestSafetyGuardFixes:
    def test_blocks_rd_with_reordered_or_slashless_flags(self):
        guard = SafetyGuard()
        assert guard.check_destructive("shell", {"command": "rd /q /s C:\\temp"}) is not None
        assert guard.check_destructive("shell", {"command": "del/f a.txt"}) is not None
        assert guard.check_destructive("shell", {"command": "DEL /Q *"}) is not None

    def test_still_allows_benign_commands(self):
        guard = SafetyGuard()
        for cmd in ("ls -la", "echo hello", "git push origin main", "cat README.md"):
            assert guard.check_destructive("shell", {"command": cmd}) is None

    def test_hallucination_detection_works_for_chinese(self):
        # 旧实现按空格切词，中文整句变成一个"关键词"，恒返回 0.0
        guard = SafetyGuard()
        claim = "大语言模型的推理能力可以通过思维链提示显著提升"
        sources = ["研究表明，思维链提示能显著提升大语言模型的推理能力。"]
        assert guard.detect_hallucination(claim, sources) > 0.3

    def test_hallucination_detection_flags_unsupported_claim(self):
        guard = SafetyGuard()
        score = guard.detect_hallucination(
            "量子计算机已经在家庭中普及使用",
            ["本文讨论大语言模型的推理能力与思维链提示。"],
        )
        assert score < 0.5

    def test_hallucination_detection_english(self):
        guard = SafetyGuard()
        score = guard.detect_hallucination(
            "chain of thought prompting improves reasoning",
            ["chain of thought prompting improves reasoning ability"],
        )
        assert score > 0.5


class TestShortTermCompression:
    def test_needs_compression_counts_raw_messages(self):
        mem = ShortTermMemory(max_tokens=100, keep_recent=2)
        for _ in range(50):
            mem.add("user", "这是一段足够长的中文内容用于撑大 token 计数。" * 3)

        # 旧实现用已截断的 get_messages() 计数，永远达不到阈值 → 压缩永不触发
        assert mem.needs_compression() is True

    def test_no_compression_for_short_history(self):
        mem = ShortTermMemory(max_tokens=6000)
        mem.add("user", "短消息")
        assert mem.needs_compression() is False


class TestSupervisorFixes:
    def test_latin_greeting_requires_word_boundary(self):
        s = Supervisor()
        # "hi" 不能吞掉以 hi 开头的知识性问题
        assert s.route_task({"query": "higgs boson"}) == "research"
        assert s.route_task({"query": "history"}) == "research"
        # 真正的寒暄仍然直接回答
        assert s.route_task({"query": "hi"}) == "answer_direct"
        assert s.route_task({"query": "hello there"}) == "answer_direct"
        assert s.route_task({"query": "你好"}) == "answer_direct"

    def test_quality_node_and_router_call_llm_only_once(self):
        llm = MagicMock()
        llm.chat.return_value = MagicMock(content='{"pass": true}')
        s = Supervisor(llm=llm)

        out = s.quality_check_fn()({"summary": "有效报告内容" * 20, "research_data": [], "iteration": 0})

        assert out["quality_result"] == "pass"
        assert llm.chat.call_count == 1
        # 条件边读取节点已算好的结论，不再重复调用 LLM
        assert s.route_after_quality(out) == "pass"
        assert llm.chat.call_count == 1

    def test_route_after_quality_honours_fail(self):
        s = Supervisor()
        assert s.route_after_quality({"quality_result": "fail"}) == "fail"
        assert s.route_after_quality({"quality_result": "pass"}) == "pass"

    def test_failed_research_ends_without_retry(self):
        s = Supervisor(llm=MagicMock())
        assert s.check_quality({"summary": "", "research_failed": True, "iteration": 0}) == "pass"


class TestBaseUrlNormalization:
    def test_keeps_existing_version_segment(self):
        assert normalize_base_url("https://open.bigmodel.cn/api/paas/v4") == \
            "https://open.bigmodel.cn/api/paas/v4"
        assert normalize_base_url("https://generativelanguage.googleapis.com/v1beta/openai") == \
            "https://generativelanguage.googleapis.com/v1beta/openai"

    def test_appends_v1_when_missing(self):
        assert normalize_base_url("https://api.deepseek.com") == "https://api.deepseek.com/v1"

    def test_strips_endpoint_suffix(self):
        assert normalize_base_url("https://x.com/v1/chat/completions") == "https://x.com/v1"

    def test_empty_input(self):
        assert normalize_base_url("") == ""
