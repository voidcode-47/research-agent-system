# -*- coding: utf-8 -*-
"""AI 味规则检测：分数、分级、句级理由与特征统计。"""
from rules.deai_detector import detect

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
