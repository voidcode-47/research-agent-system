"""AI 文本特征检测（纯规则，不调用模型）。

分数越高越像 AI 生成：套话、机械连接、句式长度高度一致、空洞形容词、排比句。
句级分数用于前端高亮。
"""
import math
import re

_SENT_SPLIT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")

_CLICHES = [
    (re.compile(r"随着.{0,15}(发展|进步|普及|推进|提升)"), "套话「随着……发展」"),
    (re.compile(r"综上所述"), "套话「综上所述」"),
    (re.compile(r"值得注意的是"), "套话「值得注意的是」"),
    (re.compile(r"具有(重要|深远)?(意义|作用|价值)"), "套话「具有重要意义/作用」"),
    (re.compile(r"发挥(着)?(重要)?作用"), "套话「发挥着重要作用」"),
    (re.compile(r"极大地|有效地|进一步|显著地"), "程度副词堆砌"),
    (re.compile(r"不仅.{0,25}而且"), "套话「不仅……而且」"),
    (re.compile(r"为.{0,20}提供(了)?(有力|重要)?(支撑|参考|保障)"), "套话「提供了有力支撑/参考」"),
]

_CONNECTORS = ("首先", "其次", "再次", "最后", "一方面", "另一方面", "此外", "同时", "总之")

_EMPTY_ADJ = (
    "显著", "重要", "关键", "巨大", "明显", "深入", "全面", "有效", "充分",
    "丰富", "极大", "深远", "广泛", "良好", "优异", "卓越", "完善", "极大",
)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.findall(text or "") if s.strip()]


def _cv(values: list[int]) -> float:
    """变异系数（总体标准差 / 均值）。"""
    vals = [v for v in values if v >= 0]
    if len(vals) < 2:
        return 1.0
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return 1.0
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) / mean


def _sentence_score(sent: str, starts: list[str], idx: int) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 12
    for pat, label in _CLICHES:
        if pat.search(sent):
            score += 18
            reasons.append(label)
    if any(sent.startswith(c) for c in _CONNECTORS):
        score += 8
        reasons.append("机械连接词开头")
    adj_hits = [a for a in _EMPTY_ADJ if a in sent]
    if adj_hits:
        score += min(16, 4 * len(adj_hits))
        reasons.append("空洞形容词：" + "、".join(adj_hits[:4]))
    # 排比：连续三句同开头（前 2 字）
    if idx >= 2 and starts[idx][:2] and starts[idx][:2] == starts[idx - 1][:2] == starts[idx - 2][:2]:
        score += 15
        reasons.append("连续三句同开头（排比）")
    # AI 常见的长句均匀节奏
    if 30 <= len(sent) <= 80 and not reasons:
        score += 4
        reasons.append("句式长度均匀")
    return max(0, min(100, score)), reasons


def detect(text: str) -> dict:
    """检测文本的 AI 味，返回整体分、句级分与特征统计。"""
    src = (text or "").strip()
    if not src:
        return {
            "score": 0, "level": "low", "sentences": [], "features": {},
            "suggestion": "文本为空。",
        }

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", src) if p.strip()]
    sentences = _split_sentences(src)
    starts = [s[:2] for s in sentences]

    sent_items = []
    for i, s in enumerate(sentences):
        sc, reasons = _sentence_score(s, starts, i)
        sent_items.append({"text": s, "score": sc, "reasons": reasons})

    avg_sent_score = sum(x["score"] for x in sent_items) / len(sent_items) if sent_items else 0

    cliche_count = sum(1 for s in sentences for pat, _ in _CLICHES if pat.search(s))
    connector_count = sum(1 for s in sentences if any(s.startswith(c) for c in _CONNECTORS))
    empty_adj_count = sum(1 for s in sentences if any(a in s for a in _EMPTY_ADJ))
    sent_cv = _cv([len(s) for s in sentences])
    para_cv = _cv([len(p) for p in paragraphs])
    parallelism = 0
    for i in range(2, len(sentences)):
        if starts[i][:2] and starts[i][:2] == starts[i - 1][:2] == starts[i - 2][:2]:
            parallelism += 1

    char_count = max(1, len(re.sub(r"\s", "", src)))
    density = cliche_count * 100.0 / char_count  # 每百字套话数

    score = avg_sent_score * 0.55
    score += min(18, cliche_count * 3)
    score += min(10, connector_count * 2)
    if sent_cv < 0.25:
        score += 15
    elif sent_cv < 0.4:
        score += 7
    if para_cv < 0.2:
        score += 8
    score += min(10, parallelism * 5)
    score = int(max(0, min(100, round(score))))

    if score < 40:
        level = "low"
        suggestion = "AI 痕迹较弱，保留原表达即可，仅需检查个别套话。"
    elif score < 70:
        level = "medium"
        suggestion = "存在模板化表达：优先删改套话与机械连接词，并调整句长节奏。"
    else:
        level = "high"
        suggestion = "AI 味明显：建议重写套话段落，拆散排比句式，补充具体数据与论证细节。"

    features = {
        "cliche_count": cliche_count,
        "cliche_density_per_100": round(density, 2),
        "connector_count": connector_count,
        "empty_adjective_sentences": empty_adj_count,
        "sentence_length_cv": round(sent_cv, 3),
        "paragraph_length_cv": round(para_cv, 3),
        "parallelism": parallelism,
        "avg_sentence_len": round(char_count / max(1, len(sentences)), 1),
        "sentence_count": len(sentences),
    }
    return {
        "score": score,
        "level": level,
        "sentences": sent_items,
        "features": features,
        "suggestion": suggestion,
    }
