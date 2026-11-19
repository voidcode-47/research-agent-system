"""中英文 AI 文本特征检测（纯规则，不调用模型）。

规则集参考两个学术去 AI 味 skill：
- humanizer-zh-academic：理论起笔、案例总结、编号逻辑、段末总结、模糊归因、
  填充语、泛化结论、AI 高频词、排比/对仗、过度限定，以及段落级硬约束阈值。
- academic-humanizer：意义夸大、空泛强化词、公式化开头、模糊归因、过度声称、
  新颖性堆叠、AI 高频词、连接词滥用、长句从句堆叠、破折号等。

分数越高越像 AI 生成；句级分数用于前端高亮。
"""
import math
import re
from collections import Counter

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_ZH_SENT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_EN_SENT_RE = re.compile(r"[^.!?\n]+[.!?]?")
_MIX_SENT_RE = re.compile(r"[^。！？!?；;\n.!?]+[。！？!?；;.!?]?")
_EN_ABBREV = (
    "e.g.", "i.e.", "et al.", "Fig.", "Figs.", "Eq.", "Eqs.", "Ref.", "Refs.",
    "vs.", "cf.", "approx.", "Sec.", "Tab.", "No.", "Dr.", "Prof.",
)

_ZH_CONNECTORS = (
    "首先", "其次", "再次", "最后", "一方面", "另一方面", "此外", "同时", "总之",
)

_ZH_EMPTY_ADJ = (
    "显著", "重要", "关键", "巨大", "明显", "深入", "全面", "有效", "充分",
    "丰富", "极大", "深远", "广泛", "良好", "优异", "卓越", "完善",
    "至关重要", "不可或缺",
)

_ZH_PATTERNS = [
    (re.compile(r"随着.{0,15}(发展|进步|普及|推进|提升)"), "套话「随着……发展」", 18),
    (re.compile(r"综上所述|由此可见|不难发现|可以看出|因此可以得出结论"), "段末总结套话", 16),
    (re.compile(r"此案例(印证|挑战|揭示)了|这提示我们|从中可以看出"), "案例总结套路", 16),
    (re.compile(
        r"具有(重要|深远|重大)(的)?(理论|现实|实践)?(意义|作用|价值)|"
        r"具有(重要|深远|重大)意义|意义深远|前景广阔|未来可期|"
        r"开辟了新方向|提供了新思路|指明了方向"
    ), "泛化意义/展望", 18),
    (re.compile(r"(发挥|起)(着)?.{0,6}作用"), "套话「发挥重要作用」", 16),
    (re.compile(r"^(依据|基于|根据|按照|遵循).{0,30}(理论|框架|观点|原则)"), "理论框架起笔", 12),
    (re.compile(r"该(处理|设计|决策|选择|方法|方案)(体现|基于|反映|印证)了?|上述选择印证了"), "被动分析套话", 12),
    (re.compile(r"面临的核心问题是|核心挑战在于|主要矛盾体现在"), "模板化问题陈述", 12),
    (re.compile(r"作为.{0,15}(载体|桥梁|纽带|基石)|扮演着.{0,12}角色|充当着.{0,12}功能"), "回避系动词「是」", 12),
    (re.compile(r"不仅.{0,25}而且"), "套话「不仅……而且」", 10),
    (re.compile(r"为.{0,20}提供(了)?(有力|重要)?(支撑|参考|保障)"), "套话「提供支撑/参考」", 12),
    (re.compile(r"值得注意|需要指出|众所周知|显而易见"), "填充引导语", 8),
    (re.compile(r"极大地|有效地|进一步|显著地|充分地|全面地"), "程度副词堆砌", 6),
    (re.compile(
        r"专家(认为|指出)|研究表明|业内(普遍)?认为|有观点认为|"
        r"一些学者(指出|认为)|学界(普遍)?认为"
    ), "模糊归因（无具体来源）", 14),
    (re.compile(r"在一定程度上|潜在地|某种程度|或多或少|在某种意义上"), "过度限定", 8),
    (re.compile(r"至关重要|不可或缺"), "空泛重要性", 8),
]

_ZH_ANGLE_RE = re.compile(r"从.{2,8}(角度|维度|层面)(来)?[看说]")
_ZH_GENERIC_RE = re.compile(
    r"具有重要意义|意义深远|前景广阔|未来可期|提供了新思路|开辟了新方向|"
    r"指明了方向|具有重要的理论意义"
)
_ZH_AI_WORDS = (
    "深刻揭示", "具有重要意义", "综合运用", "不可或缺", "深入探讨", "系统梳理",
    "提供了理论支撑", "有效解决", "充分说明", "至关重要",
)

_EN_HYPE_RE = re.compile(
    r"\b(paves? the way for|opens? (up )?new avenues?|sheds? light on|"
    r"bridges? the gap|of paramount importance|revolutioni[sz](e|es|ed|ing)|"
    r"pivotal (role|step|moment)|crucial step toward|transformative)\b", re.I
)
_EN_OPENER_RE = re.compile(
    r"^(in recent years|with the (rapid )?(development|growth|advancement) of|"
    r"despite recent advances|over the past (few|several) years)\b", re.I
)
_EN_BOILER_RE = re.compile(
    r"\b(it is worth noting that|it should be (noted|emphasized) that|notably|importantly)\b", re.I
)
_EN_VAGUE_RE = re.compile(
    r"\b(experts? (argue|believe|say|suggest)|studies (show|suggest|indicate)|"
    r"research (shows|suggests|indicates)|many (researchers|scholars) (argue|believe)|"
    r"it is widely (known|believed))\b", re.I
)
_EN_OVERCLAIM_RE = re.compile(
    r"\b(demonstrat(e|es|ed|ing)|prove[sd]?|establish(es|ed)?|confirm(s|ed)?|guarantee[sd]?)\b", re.I
)
_EN_NOVELTY_RE = re.compile(r"\b(to the best of our knowledge|for the first time)\b", re.I)
_EN_NOVEL_WORD_RE = re.compile(r"\bnovel\b", re.I)
_EN_INTENSIFIER_RE = re.compile(
    r"\b(extensive|comprehensive|thorough)\s+experiments?\b|\ba wide range of\b|"
    r"\bnumerous\b|\bvarious\b", re.I
)
_EN_AI_VOCAB_RE = re.compile(
    r"\b(delv(e|es|ed|ing)|underscor(e|es|ed|ing)|intricate|tapestry|testament|"
    r"showcas(e|es|ed|ing)|foster(s|ed|ing)?|leverag(e|es|ed|ing)|realm|"
    r"seamless(ly)?|landscape)\b", re.I
)
_EN_HEDGE_RE = re.compile(r"\b(somewhat|relatively|fairly|to some extent|quite)\b", re.I)
_EN_NEG_PARALLEL_RE = re.compile(r"\bnot (just|only)\b[^.]{0,80}\bbut\b", re.I)
_EN_CONNECTOR_RE = re.compile(
    r"^(moreover|furthermore|additionally|in particular|notably|importantly|"
    r"consequently|overall|in summary|to conclude)\b", re.I
)
_EN_SUBORD_RE = re.compile(r"\b(which|that|while|whereas|although|because|whereby)\b", re.I)
_EN_CITE_RE = re.compile(
    r"\(\s*[A-Z][A-Za-z\-]+(?:\s+et al\.)?,?\s*\d{4}\s*\)|\[\d+(?:\s*[,–-]\s*\d+)*\]"
)

_SEVERITIES = ("high", "medium", "low")


def detect_language(text: str) -> str:
    """按中日韩字符与拉丁字母占比判断主语言（zh / en / mixed）。"""
    src = text or ""
    cjk = len(_CJK_RE.findall(src))
    latin = len(_LATIN_RE.findall(src))
    if cjk == 0:
        return "en" if latin else "zh"
    if latin == 0:
        return "zh"
    ratio = cjk / (cjk + latin)
    if ratio >= 0.3:
        return "zh"
    if ratio <= 0.1:
        return "en"
    return "mixed"


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]


def _split_sentences(text: str, lang: str) -> list[str]:
    if lang == "zh":
        return [s.strip() for s in _ZH_SENT_RE.findall(text or "") if s.strip()]

    protected = text or ""
    for abbr in _EN_ABBREV:
        protected = re.sub(re.escape(abbr), abbr.replace(".", "\u00a7"), protected, flags=re.I)
    protected = re.sub(
        r"(\d)\.(\d)",
        lambda m: m.group(1) + "\u00a7" + m.group(2),
        protected,
    )
    pattern = _EN_SENT_RE if lang == "en" else _MIX_SENT_RE
    return [
        s.replace("\u00a7", ".").strip()
        for s in pattern.findall(protected) if s.strip()
    ]


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


def _score_zh(items: list[dict], idx: int, prev_connector: bool) -> dict:
    sent = items[idx]["text"]
    reasons: list[str] = []
    hits: list[tuple[str, int]] = []

    def add(label: str, weight: int) -> None:
        hits.append((label, weight))
        reasons.append(label)

    for pat, label, weight in _ZH_PATTERNS:
        if not pat.search(sent):
            continue
        w = weight
        if label in ("段末总结套话", "案例总结套路") and not items[idx]["is_para_end"]:
            w = max(6, w - 6)
        add(label, w)

    if len(re.findall(r"[^，。；]{2,12}了", sent)) >= 3 and sent.count("，") >= 2:
        add("排比/对仗句式", 12)
    if len(sent) >= 80 and sent.count("，") >= 3:
        add("长句堆叠", 8)

    conn = next((c for c in _ZH_CONNECTORS if sent.startswith(c)), "")
    if conn:
        add("机械连接词开头", 8)
        if prev_connector:
            add("连续连接词并列", 6)

    adj_hits = [a for a in _ZH_EMPTY_ADJ if a in sent]
    if adj_hits:
        unit = 2 if re.search(r"\d", sent) else 4
        add("空洞形容词：" + "、".join(adj_hits[:4]), min(16, unit * len(adj_hits)))

    if idx >= 2:
        starts = [items[i]["start"] for i in (idx - 2, idx - 1, idx)]
        if starts[2] and starts[0] == starts[1] == starts[2]:
            add("连续三句同开头（排比）", 15)

    if not hits and 30 <= len(sent) <= 80:
        add("句式长度均匀", 4)

    score = 10 + sum(w for _, w in hits)
    return {
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "hits": hits,
        "connector": bool(conn),
        "strong": sum(1 for _, w in hits if w >= 12),
        "empty": bool(adj_hits),
        "long": "长句堆叠" in reasons,
    }


def _score_en(items: list[dict], idx: int, prev_connector: bool) -> dict:
    sent = items[idx]["text"]
    lower = sent.lower()
    reasons: list[str] = []
    hits: list[tuple[str, int]] = []
    has_digit = bool(re.search(r"\d", sent))
    has_cite = bool(_EN_CITE_RE.search(sent))

    def add(label: str, weight: int) -> None:
        hits.append((label, weight))
        reasons.append(label)

    if _EN_HYPE_RE.search(sent):
        add("意义/前景夸大", 16)
    if _EN_OPENER_RE.search(sent):
        add("公式化开头", 14)
    if _EN_BOILER_RE.search(sent):
        add("冗余强调语", 12)
    if _EN_VAGUE_RE.search(sent) and not has_cite:
        add("模糊归因（无具体来源）", 14)
    if _EN_OVERCLAIM_RE.search(sent) and not has_digit and not has_cite:
        add("过度声称（缺少数据/引用）", 10)
    if _EN_NOVELTY_RE.search(sent):
        add("新颖性堆砌", 10)
    elif _EN_NOVEL_WORD_RE.search(sent):
        add("新颖性堆砌", 5)
    if _EN_INTENSIFIER_RE.search(sent):
        add("空泛强化词", 8)
    if _EN_AI_VOCAB_RE.search(sent):
        add("AI 高频词", 6)
    if _EN_HEDGE_RE.search(sent):
        add("模糊限定词", 6)
    if _EN_NEG_PARALLEL_RE.search(sent):
        add("否定式排比 not just…but", 8)

    if (
        re.search(r"\bsignificant(ly)?\b", lower)
        and not has_digit
        and not re.search(r"\bp\s*[<=>]", lower)
    ):
        add("「显著」缺少检验或数据", 8)

    em_dashes = sent.count("—") + sent.count("–")
    if em_dashes:
        add("破折号（建议改写为逗号/冒号）", 8)

    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", sent))
    subords = len(_EN_SUBORD_RE.findall(lower))
    long_sentence = False
    if words >= 45:
        long_sentence = True
        add("长句从句堆叠", 10)
    elif words >= 30 and subords >= 2:
        long_sentence = True
        add("长句从句堆叠", 8)
    elif words >= 30:
        add("长句偏长", 4)

    conn = _EN_CONNECTOR_RE.match(sent)
    if conn:
        add("机械连接词开头", 8)
        if prev_connector:
            add("连续连接词并列", 6)

    if idx >= 2:
        starts = [items[i]["start"] for i in (idx - 2, idx - 1, idx)]
        if starts[2] and len(starts[2]) > 1 and starts[0] == starts[1] == starts[2]:
            add("连续三句同开头（排比）", 12)

    if not hits and 15 <= words <= 35:
        add("句式长度均匀", 4)

    score = 6 + sum(w for _, w in hits)
    return {
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "hits": hits,
        "connector": bool(conn),
        "strong": sum(1 for _, w in hits if w >= 12),
        "empty": any(l in ("空泛强化词", "意义/前景夸大", "AI 高频词", "模糊限定词") for l, _ in hits),
        "long": long_sentence,
        "em_dashes": em_dashes,
    }


def _paragraph_stats(lang: str, paragraphs: list[str], items: list[dict]) -> list[dict]:
    stats = []
    for p_idx, para in enumerate(paragraphs):
        p_items = [it for it in items if it["paragraph"] == p_idx]
        seen: set[str] = set()
        hits: list[str] = []
        points = 0.0
        for it in p_items:
            for label, weight in it["_hits"]:
                if label in seen:
                    continue
                seen.add(label)
                hits.append(label)
                points += 2 if weight >= 14 else (1 if weight >= 8 else 0.5)

        ai_word_hits = 0
        if lang in ("zh", "mixed"):
            if len(_ZH_ANGLE_RE.findall(para)) >= 2 and "三维等重分析" not in seen:
                seen.add("三维等重分析")
                hits.append("三维等重分析")
                points += 2
            ai_word_hits = sum(1 for w in _ZH_AI_WORDS if w in para)
            if ai_word_hits > 2 and "AI 高频词密度偏高" not in seen:
                hits.append("AI 高频词密度偏高")
                points += 1
        if lang in ("en", "mixed"):
            if sum(1 for it in p_items if it["_connector"]) >= 2 and "连续连接词开头" not in seen:
                hits.append("连续连接词开头")
                points += 1
            if para.count("—") + para.count("–") >= 2 and "破折号密集" not in seen:
                hits.append("破折号密集")
                points += 1
            long_ratio = sum(1 for it in p_items if it.get("_long")) / max(1, len(p_items))
            if long_ratio >= 0.5 and "长句占比偏高" not in seen:
                hits.append("长句占比偏高")
                points += 1

        risk_points = int(round(points))
        risk = "high" if risk_points >= 4 else ("medium" if risk_points >= 2 else "low")
        stats.append({
            "index": p_idx,
            "risk_points": risk_points,
            "risk": risk,
            "hits": hits,
            "excerpt": para[:80],
            "chars": len(re.sub(r"\s", "", para)),
            "ai_word_hits": ai_word_hits,
        })
    return stats


def _build_constraints(lang: str, paragraphs: list[str], items: list[dict],
                       para_stats: list[dict]) -> list[dict]:
    failed: list[dict] = []

    def add(cid: str, label: str, count: int, limit: str) -> None:
        failed.append({"id": cid, "label": label, "count": count, "limit": limit})

    vague = sum(
        1 for it in items
        if any(label.startswith("模糊归因") for label, _ in it["_hits"])
    )
    if lang in ("zh", "mixed"):
        if vague:
            add("zh_vague", "模糊归因（无具体来源）", vague, "0 处")
        end_formula = sum(
            1 for it in items
            if it["is_para_end"]
            and any(label in ("段末总结套话", "案例总结套路") for label, _ in it["_hits"])
        )
        if end_formula > 1:
            add("zh_end_formula", "段末总结套句", end_formula, "≤1 处")
        if paragraphs and _ZH_GENERIC_RE.search(paragraphs[-1]):
            add("zh_generic_ending", "泛化结尾", 1, "0 处")
        theory_paras = sum(
            1 for it in items
            if it["is_para_start"]
            and any(label == "理论框架起笔" for label, _ in it["_hits"])
        )
        ratio = theory_paras / max(1, len(paragraphs))
        if ratio > 0.2:
            add("zh_theory_openers", "理论起笔段落占比", round(ratio * 100), "≤20%")
        dense = sum(1 for p in para_stats if p["ai_word_hits"] > 2)
        if dense:
            add("zh_ai_word_density", "AI 高频词超标的段落", dense, "每段 ≤2 个")
    if lang in ("en", "mixed"):
        if vague:
            add("en_vague", "模糊归因（无具体来源）", vague, "0 处")
        consecutive = sum(
            1 for it in items
            if any(label == "连续连接词并列" for label, _ in it["_hits"])
        )
        if consecutive:
            add("en_connectives", "连续连接词开头", consecutive, "0 处")
        em_dashes = sum(it.get("_em_dashes", 0) for it in items)
        if em_dashes:
            add("en_em_dash", "破折号", em_dashes, "0 处")
        overclaim = sum(
            1 for it in items
            if any(label.startswith("过度声称") for label, _ in it["_hits"])
        )
        if overclaim:
            add("en_overclaim", "过度声称（缺少证据）", overclaim, "0 处")
        long_sents = sum(1 for it in items if it.get("_long"))
        long_ratio = long_sents / max(1, len(items))
        if long_ratio > 0.2:
            add("en_long_sentences", "长句占比", round(long_ratio * 100), "≤20%")
    return failed


def _analyze(text: str) -> dict:
    src = (text or "").strip()
    lang = detect_language(src)
    paragraphs = _split_paragraphs(src)
    items: list[dict] = []

    for p_idx, para in enumerate(paragraphs):
        sents = _split_sentences(para, lang)
        for s_idx, sent in enumerate(sents):
            items.append({
                "text": sent,
                "paragraph": p_idx,
                "is_para_start": s_idx == 0,
                "is_para_end": s_idx == len(sents) - 1,
            })

    for it in items:
        sent = it["text"]
        if lang == "zh":
            it["start"] = sent[:2]
        else:
            match = re.match(r"[A-Za-z]+", sent)
            it["start"] = match.group(0).lower() if match else sent[:2]

    prev_para = -1
    prev_connector = False
    for idx, it in enumerate(items):
        if it["paragraph"] != prev_para:
            prev_para = it["paragraph"]
            prev_connector = False
        if lang == "zh":
            result = _score_zh(items, idx, prev_connector)
        elif lang == "en":
            result = _score_en(items, idx, prev_connector)
        else:
            zh = _score_zh(items, idx, prev_connector)
            en = _score_en(items, idx, prev_connector)
            result = zh if zh["score"] >= en["score"] else dict(en)
            other = en if result is zh else zh
            if other["hits"]:
                result = dict(result)
                result["reasons"] = list(dict.fromkeys(result["reasons"] + other["reasons"]))
                result["hits"] = result["hits"] + other["hits"]
                result["strong"] += other["strong"]
                result["connector"] = result["connector"] or other["connector"]
                result["empty"] = result["empty"] or other["empty"]
                result["long"] = result["long"] or other["long"]
                result["em_dashes"] = result.get("em_dashes", 0) + other.get("em_dashes", 0)

        it["score"] = int(result["score"])
        it["reasons"] = result["reasons"]
        it["_hits"] = result["hits"]
        it["_connector"] = bool(result["connector"])
        it["_strong"] = int(result["strong"])
        it["_empty"] = bool(result["empty"])
        it["_long"] = bool(result.get("long"))
        it["_em_dashes"] = int(result.get("em_dashes") or 0)
        prev_connector = it["_connector"]

    para_stats = _paragraph_stats(lang, paragraphs, items)
    constraints = _build_constraints(lang, paragraphs, items, para_stats)
    pattern_counts = Counter(label for it in items for label, _ in it["_hits"])

    return {
        "language": lang,
        "paragraphs": paragraphs,
        "items": items,
        "para_stats": para_stats,
        "constraints": constraints,
        "pattern_counts": dict(pattern_counts),
    }


def _features(analysis: dict) -> dict:
    lang = analysis["language"]
    items = analysis["items"]
    paragraphs = analysis["paragraphs"]
    para_stats = analysis["para_stats"]
    src_chars = sum(len(re.sub(r"\s", "", it["text"])) for it in items) or 1

    cliche_count = sum(1 for it in items if it["_strong"] > 0)
    connector_count = sum(1 for it in items if it["_connector"])
    empty_adj_count = sum(1 for it in items if it["_empty"])
    parallelism = sum(
        1 for idx in range(2, len(items))
        if items[idx]["start"]
        and items[idx]["start"] == items[idx - 1]["start"] == items[idx - 2]["start"]
    )
    sent_cv = _cv([len(re.sub(r"\s", "", it["text"])) for it in items])
    para_cv = _cv([len(re.sub(r"\s", "", p)) for p in paragraphs])
    vague_count = sum(
        1 for it in items
        if any(label.startswith("模糊归因") for label, _ in it["_hits"])
    )
    end_formula_count = sum(
        1 for it in items
        if it["is_para_end"]
        and any(label in ("段末总结套话", "案例总结套路") for label, _ in it["_hits"])
    )
    long_count = sum(1 for it in items if it["_long"])
    em_dashes = sum(it["_em_dashes"] for it in items)

    return {
        "language": lang,
        "cliche_count": cliche_count,
        "cliche_density_per_100": round(cliche_count * 100.0 / src_chars, 2),
        "connector_count": connector_count,
        "empty_adjective_sentences": empty_adj_count,
        "sentence_length_cv": round(sent_cv, 3),
        "paragraph_length_cv": round(para_cv, 3),
        "parallelism": parallelism,
        "avg_sentence_len": round(src_chars / max(1, len(items)), 1),
        "sentence_count": len(items),
        "vague_attribution_count": vague_count,
        "paragraph_end_formula_count": end_formula_count,
        "long_sentence_ratio": round(long_count / max(1, len(items)), 3),
        "em_dash_count": em_dashes,
        "pattern_hits": sum(len(it["_hits"]) for it in items),
        "high_risk_paragraphs": sum(1 for p in para_stats if p["risk"] == "high"),
    }


def _suggestion(lang: str, level: str) -> str:
    if lang == "en":
        if level == "low":
            return "AI 痕迹较弱，保留原表达即可，仅需检查个别过度声称与空泛强化词。"
        if level == "medium":
            return (
                "存在模板化表达：优先删除意义夸大与空泛强化词，把结论与数字或引用绑定，"
                "拆分从句堆叠的长句。"
            )
        return (
            "AI 痕迹明显：重写公式化开头与意义夸大段落，补齐证据指向，"
            "打破排比与长句堆叠，并删除破折号。"
        )
    if level == "low":
        return "AI 痕迹较弱，保留原表达即可，仅需检查个别套话。"
    if level == "medium":
        return "存在模板化表达：优先删改套话与机械连接词，并调整句长节奏。"
    return "AI 味明显：建议重写套话段落，拆散排比句式，补充具体数据与论证细节。"


def detect(text: str) -> dict:
    """检测文本的 AI 味，返回整体分、句级分、段落风险与特征统计。"""
    src = (text or "").strip()
    if not src:
        return {
            "score": 0, "level": "low", "language": "zh", "sentences": [],
            "features": {}, "paragraphs": [], "constraints": [],
            "pattern_counts": {}, "suggestion": "文本为空。",
        }

    analysis = _analyze(src)
    features = _features(analysis)
    items = analysis["items"]

    avg_sent_score = sum(it["score"] for it in items) / len(items) if items else 0
    sent_cv = features["sentence_length_cv"]
    para_cv = features["paragraph_length_cv"]
    high_ratio = features["high_risk_paragraphs"] / max(1, len(analysis["paragraphs"]))

    score = avg_sent_score * 0.55
    score += min(18, features["cliche_count"] * 2.5)
    score += min(10, features["connector_count"] * 2)
    if sent_cv < 0.25:
        score += 15
    elif sent_cv < 0.4:
        score += 7
    if para_cv < 0.2:
        score += 8
    score += min(10, features["parallelism"] * 5)
    score += min(10, high_ratio * 12)
    if analysis["language"] in ("en", "mixed"):
        score += min(8, features["long_sentence_ratio"] * 12)
        score += min(6, features["em_dash_count"] * 3)
    score = int(max(0, min(100, round(score))))

    if score < 40:
        level = "low"
    elif score < 70:
        level = "medium"
    else:
        level = "high"

    sentences = [
        {"text": it["text"], "score": it["score"], "reasons": it["reasons"]}
        for it in items
    ]
    paragraphs = [
        {
            "index": p["index"], "risk": p["risk"], "risk_points": p["risk_points"],
            "hits": p["hits"], "excerpt": p["excerpt"], "chars": p["chars"],
        }
        for p in analysis["para_stats"]
    ]
    return {
        "score": score,
        "level": level,
        "language": analysis["language"],
        "sentences": sentences,
        "features": features,
        "paragraphs": paragraphs,
        "constraints": analysis["constraints"],
        "pattern_counts": analysis["pattern_counts"],
        "suggestion": _suggestion(analysis["language"], level),
    }


def estimate_ai_rate(text: str) -> dict:
    """估算 AI 生成率（%，纯规则参考值），支持中英文。

    以「明显 AI 特征句」的正文占比、高风险段落占比为基础，叠加句长
    均匀、长句堆叠、破折号与特征密度惩罚。返回可疑句子明细。
    """
    src = (text or "").strip()
    if not src:
        return {"ai_rate": 0, "level": "low", "segments": [], "features": {}}

    analysis = _analyze(src)
    features = _features(analysis)
    items = analysis["items"]
    lang = analysis["language"]
    total_chars = sum(len(re.sub(r"\s", "", it["text"])) for it in items) or 1
    threshold = 45 if lang == "zh" else 35

    flagged = [it for it in items if it["score"] >= threshold]
    flagged_chars = sum(len(re.sub(r"\s", "", it["text"])) for it in flagged)
    high_chars = sum(
        p["chars"] for p in analysis["para_stats"] if p["risk"] == "high"
    )

    rate = 55.0 * flagged_chars / total_chars
    rate += 20.0 * high_chars / total_chars

    len_cv = features["sentence_length_cv"]
    if len_cv < 0.25:
        rate += 8
    elif len_cv < 0.4:
        rate += 4
    if lang in ("en", "mixed"):
        rate += min(8, features["long_sentence_ratio"] * 20)
    rate += min(10, features["pattern_hits"] * 1000.0 / total_chars * 1.2)
    rate = int(max(0, min(100, round(rate))))

    if rate < 30:
        level = "low"
    elif rate < 60:
        level = "medium"
    else:
        level = "high"

    segments = sorted(flagged, key=lambda x: x["score"], reverse=True)[:6]
    features["ai_like_sentences"] = len(flagged)
    features["ai_like_chars"] = flagged_chars
    features["total_chars"] = total_chars
    return {
        "ai_rate": rate,
        "level": level,
        "segments": [
            {"text": it["text"], "score": it["score"], "reasons": it["reasons"],
             "chars": len(re.sub(r"\s", "", it["text"]))}
            for it in segments
        ],
        "features": features,
    }
