# -*- coding: utf-8 -*-
"""AI 味规则检测：分数、分级、句级理由与特征统计。"""
from rules.deai_detector import detect, detect_language, estimate_ai_rate

HUMAN = (
    "实验在 RTX 4090 上训练了 12 小时。"
    "测试集包含 500 张图像，其中 120 张来自公开数据集。"
    "加入注意力模块后，准确率从 91.2% 提升到 93.5%。"
    "但小目标检测的召回率仍然偏低。"
)

AI_STYLE = (
    "随着人工智能技术的快速发展，深度学习在多个领域发挥着重要作用。"
    "首先，值得注意的是，该方法具有重要的理论意义。"
    "其次，该方案能够有效地提升模型性能。"
    "最后，综上所述，研究为后续工作提供了有力支撑。"
)

EN_HUMAN = (
    "We train ResNet-50 on ImageNet for 90 epochs. "
    "The baseline reaches 76.1% top-1 accuracy. "
    "Adding the attention block raises it to 78.4% with the same compute budget. "
    "Gains shrink on the long-tail subset, where accuracy drops by 1.1 points."
)

EN_AI = (
    "In recent years, deep learning has attracted increasing attention. "
    "Moreover, our novel framework demonstrates a pivotal breakthrough and paves "
    "the way for future research. "
    "Furthermore, extensive experiments on a wide range of datasets show "
    "significant improvements. "
    "It is worth noting that the method also sheds light on a problem of "
    "paramount importance."
)

ZH_ACADEMIC_AI = (
    "依据社会建构主义理论，知识通过社会互动建构而成。"
    "此案例印证了目的论的核心观点，这提示我们，翻译决策需基于目标读者。"
    "专家认为，社交媒体在政治动员中发挥着至关重要的作用。"
    "综上所述，本研究具有重要的理论意义，为相关领域提供了新的思路。"
)


def test_empty_text():
    r = detect("")
    assert r["score"] == 0
    assert r["level"] == "low"
    assert r["sentences"] == []


def test_ai_text_scores_higher_than_human():
    human = detect(HUMAN)
    ai = detect(AI_STYLE)
    assert ai["score"] > human["score"]
    assert human["level"] == "low"
    assert ai["level"] in ("medium", "high")
    assert ai["features"]["cliche_count"] >= 3
    assert ai["features"]["connector_count"] >= 2


def test_sentence_level_reasons():
    r = detect(AI_STYLE)
    assert len(r["sentences"]) == 4
    first = r["sentences"][0]
    assert first["score"] > 0
    assert any("随着" in x for x in first["reasons"])
    # 每句都有 0~100 的分数
    assert all(0 <= s["score"] <= 100 for s in r["sentences"])


def test_connectors_and_empty_adjectives():
    r = detect("首先，该结果具有显著优势。其次，方法十分重要。")
    assert r["features"]["connector_count"] >= 1
    assert r["features"]["empty_adjective_sentences"] >= 1


def test_parallelism_detected():
    text = "我们提出了方法甲。我们改进了模块乙。我们验证了系统丙。"
    r = detect(text)
    assert r["features"]["parallelism"] >= 1
    assert any("排比" in x for s in r["sentences"] for x in s["reasons"])


def test_features_shape():
    r = detect(HUMAN)
    f = r["features"]
    for key in ("cliche_count", "cliche_density_per_100", "connector_count",
                "empty_adjective_sentences", "sentence_length_cv",
                "paragraph_length_cv", "parallelism", "avg_sentence_len",
                "sentence_count"):
        assert key in f
    assert f["sentence_count"] == 4
    assert r["suggestion"]


def test_uniform_sentence_length_gets_bonus():
    """三句长度接近且无其他特征时，均匀节奏会拉高分数。"""
    text = "甲" * 20 + "。" + "乙" * 20 + "。" + "丙" * 20 + "。"
    r = detect(text)
    assert r["features"]["sentence_length_cv"] < 0.25
    assert r["score"] >= 15


def test_estimate_ai_rate_orders_human_below_ai():
    human = estimate_ai_rate(HUMAN)
    ai = estimate_ai_rate(AI_STYLE)
    assert human["ai_rate"] < ai["ai_rate"]
    assert 0 <= human["ai_rate"] <= 100
    assert 0 <= ai["ai_rate"] <= 100
    assert ai["level"] in ("medium", "high")
    assert ai["segments"]
    assert all({"text", "score", "reasons"} <= set(s) for s in ai["segments"])


def test_estimate_ai_rate_empty():
    r = estimate_ai_rate("")
    assert r["ai_rate"] == 0
    assert r["segments"] == []


def test_language_detection():
    assert detect_language("中文论文内容，模型训练和实验结果。") == "zh"
    assert detect_language("We train a model on three datasets.") == "en"
    assert detect_language("") == "zh"
    assert detect(EN_AI)["language"] == "en"


def test_english_ai_scores_higher_than_human():
    human = detect(EN_HUMAN)
    ai = detect(EN_AI)
    assert ai["score"] > human["score"]
    assert human["level"] == "low"
    assert ai["level"] in ("medium", "high")
    assert ai["features"]["pattern_hits"] > human["features"]["pattern_hits"]
    assert ai["features"]["sentence_count"] == 4


def test_english_specific_tells():
    r = detect(
        "Moreover, our novel framework demonstrates a pivotal breakthrough "
        "and paves the way for future research."
    )
    reasons = " ".join(r["sentences"][0]["reasons"])
    assert "意义/前景夸大" in reasons
    assert "过度声称（缺少数据/引用）" in reasons
    assert "机械连接词开头" in reasons


def test_english_vague_attribution_requires_source():
    with_source = detect("Studies show a 12% gain (Smith et al., 2023).")
    without = detect("Studies show that the method is effective.")
    assert "模糊归因（无具体来源）" not in " ".join(with_source["sentences"][0]["reasons"])
    assert "模糊归因（无具体来源）" in " ".join(without["sentences"][0]["reasons"])


def test_english_em_dash_and_long_sentence():
    dash = detect(
        "This result — which holds across three datasets — demonstrates the "
        "broad utility of the approach."
    )
    assert "破折号（建议改写为逗号/冒号）" in " ".join(dash["sentences"][0]["reasons"])

    long = detect(
        "In this work we introduce a framework that combines retrieval, reranking, "
        "and calibration, which improves accuracy on three benchmarks while reducing "
        "latency, and that also generalizes to the long-tail split, although the "
        "gains are smaller there and depend on the retrieval budget."
    )
    assert "长句从句堆叠" in " ".join(long["sentences"][0]["reasons"])


def test_estimate_ai_rate_english_orders_human_below_ai():
    human = estimate_ai_rate(EN_HUMAN)
    ai = estimate_ai_rate(EN_AI)
    assert human["ai_rate"] < ai["ai_rate"]
    assert ai["ai_rate"] >= 50
    assert all({"text", "score", "reasons"} <= set(s) for s in ai["segments"])


def test_chinese_academic_skill_patterns():
    r = detect(ZH_ACADEMIC_AI)
    labels = " ".join(x for s in r["sentences"] for x in s["reasons"])
    assert "理论框架起笔" in labels
    assert "案例总结套路" in labels
    assert "模糊归因（无具体来源）" in labels
    assert "段末总结套话" in labels
    assert "泛化意义/展望" in labels
    assert r["features"]["vague_attribution_count"] >= 1


def test_paragraph_risk_and_constraints():
    r = detect(ZH_ACADEMIC_AI)
    assert r["paragraphs"][0]["risk"] == "high"
    assert r["paragraphs"][0]["risk_points"] >= 4
    ids = {c["id"] for c in r["constraints"]}
    assert "zh_vague" in ids
    assert "zh_generic_ending" in ids


def test_english_paragraph_risk():
    r = detect(EN_AI)
    assert r["paragraphs"][0]["risk"] == "high"
    ids = {c["id"] for c in r["constraints"]}
    assert "en_connectives" in ids
    assert "en_overclaim" in ids
