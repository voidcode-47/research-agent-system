# -*- coding: utf-8 -*-
"""对话页快速问答路由测试：_needs_tools 规则。"""
from server import _needs_tools


class TestNeedsToolsRouting:
    def test_research_intent_triggers_deep(self):
        cases = [
            "tensorflow深度学习在工业领域的应用研究",
            "帮我写一份大模型最新进展的调研报告",
            "对比一下 qwen2.5 和 deepseek 哪个好",
            "2026 年 AI 智能体发展趋势分析",
            "查一下学术 API 的数据覆盖",
            "论文引用格式怎么规范",
        ]
        for c in cases:
            assert _needs_tools(c), f"应走深度检索: {c}"

    def test_direct_questions_fast(self):
        cases = [
            "你好", "谢谢", "好的", "在吗", "再见", "嗨", "hello", "ok",
            "什么是机器学习",
            "解释一下反向传播",
            "介绍一下你自己",
            "翻译这句话",
            "哈哈",
        ]
        for c in cases:
            assert not _needs_tools(c), f"应快速回答: {c}"

    def test_short_sentence_fast(self):
        assert not _needs_tools("今天天气")
        assert not _needs_tools("")

    def test_trim_whitespace(self):
        assert _needs_tools("  大模型研究进展  ")
        assert not _needs_tools("  hello  ")

    def test_news_and_fact_long_questions_deep(self):
        # 长句疑问（事实/时事型）→ 深度检索
        cases = [
            "昨天 AI 领域有哪些重要新闻",
            "2026 年深度学习框架市场格局是怎样的",
            "今年考研数学一和数学二有什么区别",
            "现在房价走势怎样",
        ]
        for c in cases:
            assert _needs_tools(c), f"应走深度检索: {c}"

    def test_short_concept_questions_fast(self):
        # 概念定义类短问 → 快速回答（模型知识足够，不浪费 token）
        cases = ["什么是机器学习", "tensorflow 是什么", "什么是反向传播", "介绍一下你自己"]
        for c in cases:
            assert not _needs_tools(c), f"应快速回答: {c}"

    def test_personal_help_short_fast(self):
        # 个人求助类 → 直接回答，不拉去检索
        cases = ["帮我看看这个报错", "怎么设置代理", "你能翻译一下吗", "请问怎么解决卡顿"]
        for c in cases:
            assert not _needs_tools(c), f"应快速回答: {c}"
