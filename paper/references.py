"""参考文献一致性与 GB/T 7714 著录规范化（纯本地规则，无 LLM）。

检查内容：
- REF-001 条目编号缺失、重复或与条目顺序不一致
- REF-002 参考文献条目未被正文引用
- REF-003 正文引用编号超出参考文献条数
- REF-004 正文首次引用顺序与条目编号不一致
- REF-005 GB/T 7714 条目要素缺失（类型标识、年份、[EB/OL] 的 URL 与日期）
- REF-006 重复条目（忽略编号与空白后内容相同）

规范化只做可安全自动处理的调整：编号统一为半角 [n]、全角方括号/斜杠转半角、
类型标识大写、页码区间连接符统一为半角连字符、去除多余空白并补齐句末点号。
"""
import re

from config.paper import DEFAULT_FORMAT

_BRACKET_NUM_RE = re.compile(r"^\s*[\[［]\s*(\d{1,6})\s*[\]］]\s*")
_PLAIN_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[.、]\s+")
_CITE_RE = re.compile(r"[\[［]\s*(\d{1,6})\s*[\]］]")
_TYPE_RE = re.compile(
    r"[\[［]\s*(J|M|C|D|N|S|P|R|Z|A|G|EB/OL|DB/OL|J/OL|M/OL|C/OL|N/OL|D/OL|R/OL|S/OL|P/OL|Z/OL|G/OL)\s*[\]］]",
    re.IGNORECASE,
)
_TYPE_TOKEN_RE = re.compile(r"[\[［]\s*([A-Za-z]{1,4}(?:/[A-Za-z]{2})?)\s*[\]］]")
_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_ACCESS_DATE_RE = re.compile(r"[\(（]\s*(19|20)\d{2}-\d{1,2}-\d{1,2}\s*[\)）]")
_PAGE_RANGE_RE = re.compile(r"(?<=\d)\s*[–—~～]\s*(?=\d)")
_TRAILING_SPACE_RE = re.compile(r"\s+([,.;])")

_TYPE_LABELS = {
    "J": "期刊论文", "M": "专著", "C": "会议论文", "D": "学位论文",
    "N": "报纸", "S": "标准", "P": "专利", "R": "报告", "Z": "其他",
    "A": "专著析出文献", "G": "汇编", "EB/OL": "电子资源",
}
_SEVERITY_DEDUCT = {"high": 8, "medium": 4, "low": 2}

MAX_ENTRIES = 500


def _entry_number(text: str) -> tuple[int | None, str]:
    """拆出条目编号与正文；支持 [n] 与「1.」两种常见编号。"""
    m = _BRACKET_NUM_RE.match(text or "")
    if m:
        return int(m.group(1)), text[m.end():].strip()
    m = _PLAIN_NUM_RE.match(text or "")
    if m:
        return int(m.group(1)), text[m.end():].strip()
    return None, (text or "").strip()


def _ref_type(text: str) -> str:
    m = _TYPE_RE.search(text or "")
    return m.group(1).upper() if m else ""


def normalize_entry(text: str, number: int | None = None) -> str:
    """按 GB/T 7714 的书写习惯做保守规范化，不推断缺失的出版信息。"""
    raw = (text or "").strip()
    if not raw:
        return raw
    num, body = _entry_number(raw)
    if num is None:
        num = number
    s = body.translate(str.maketrans({"［": "[", "］": "]", "／": "/"}))

    def _upper_type(m):
        token = m.group(1).upper()
        return f"[{token}]" if token in _TYPE_LABELS else m.group(0)

    s = _TYPE_TOKEN_RE.sub(_upper_type, s)
    s = _PAGE_RANGE_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _TRAILING_SPACE_RE.sub(r"\1", s)
    if num is not None:
        s = f"[{num}] {s}"
    if not s.endswith((".", "。", "?", "!", "？", "！")):
        s += "."
    return s


def _issue(code: str, severity: str, paragraph: dict, message: str,
           evidence: str = "", basis: str = "GB/T 7714 参考文献著录规则") -> dict:
    return {
        "rule_id": code,
        "severity": severity,
        "page": paragraph.get("page") or 0,
        "paragraph_id": paragraph.get("id") or "",
        "evidence": (evidence or paragraph.get("text") or "")[:120],
        "suggestion": message,
        "basis": basis,
    }


def analyze(paragraphs: list[dict], format_spec: dict | None = None) -> dict:
    """检查参考文献与正文引用的一致性，并给出逐条规范化结果。"""
    spec = dict(DEFAULT_FORMAT)
    spec.update(format_spec or {})
    style = str(spec.get("reference_style") or "gbt7714").lower()
    numeric_style = style in ("gbt7714", "ieee")
    refs = [p for p in (paragraphs or []) if p.get("type") == "reference"][:MAX_ENTRIES]
    body = [
        p for p in (paragraphs or [])
        if p.get("type") == "body" and not p.get("table_id")
    ]

    # 正文引用：记录编号与首次出现顺序（忽略超长数字，避免整数转换异常）
    first_cite: list[tuple[int, dict]] = []
    cited_numbers: set[int] = set()
    for p in body:
        for m in _CITE_RE.finditer(p.get("text") or ""):
            number = int(m.group(1))
            cited_numbers.add(number)
            if all(number != n for n, _ in first_cite):
                first_cite.append((number, p))

    entries: list[dict] = []
    issues: list[dict] = []
    number_counts: dict[int, int] = {}
    seen_numbers: list[int] = []
    seen_texts: dict[str, dict] = {}

    for idx, p in enumerate(refs):
        text = (p.get("text") or "").strip()
        number, body_text = _entry_number(text)
        fallback = number if number is not None else idx + 1
        normalized = normalize_entry(text, fallback)
        ref_type = _ref_type(text)
        problems: list[dict] = []

        if number is None:
            problems.append({"code": "REF-001", "severity": "medium",
                             "message": "缺少 [n] 条目编号，保存时将按出现顺序补号"})
        else:
            number_counts[number] = number_counts.get(number, 0) + 1
            seen_numbers.append(number)

        if numeric_style and fallback not in cited_numbers:
            problems.append({"code": "REF-002", "severity": "medium",
                             "message": "该条目未被正文引用，请补充引用或删除条目"})

        if style == "gbt7714" and not ref_type:
            problems.append({"code": "REF-005", "severity": "medium",
                             "message": "缺少文献类型标识（如 [J]/[M]/[C]/[EB/OL]）"})
        if not _YEAR_RE.search(text):
            problems.append({"code": "REF-005", "severity": "medium",
                             "message": "缺少出版年份"})
        if ref_type == "EB/OL":
            if not _URL_RE.search(text):
                problems.append({"code": "REF-005", "severity": "high",
                                 "message": "电子资源缺少访问地址（URL）"})
            if not _ACCESS_DATE_RE.search(text):
                problems.append({"code": "REF-005", "severity": "medium",
                                 "message": "电子资源缺少引用日期，格式如 (2024-01-31)"})

        # 重复条目：忽略编号与空白后比较
        key = re.sub(r"\s+", "", body_text).rstrip(".。")
        dup = seen_texts.get(key) if key else None
        if dup is not None:
            problems.append({"code": "REF-006", "severity": "medium",
                             "message": f"与 {dup.get('paragraph_id')} 内容重复"})
            dup["problems"].append({"code": "REF-006", "severity": "medium",
                                    "message": "存在重复条目"})
        elif key:
            seen_texts[key] = {"paragraph_id": p.get("id") or "", "problems": problems}

        for item in problems:
            issues.append(_issue(item["code"], item["severity"], p,
                                 item["message"], text))

        entries.append({
            "paragraph_id": p.get("id") or "",
            "page": p.get("page") or 0,
            "number": number,
            "text": text,
            "normalized": normalized,
            "changed": normalized != text,
            "type": ref_type,
            "type_label": _TYPE_LABELS.get(ref_type, ""),
            "cited": fallback in cited_numbers,
            "problems": problems,
        })

    # 编号重复与顺序
    for number, count in number_counts.items():
        if count > 1:
            for e in entries:
                if e["number"] == number and not any(
                        it["code"] == "REF-001" for it in e["problems"]):
                    e["problems"].append({
                        "code": "REF-001", "severity": "high",
                        "message": f"条目编号 [{number}] 重复",
                    })
            issues.append(_issue(
                "REF-001", "high", refs[0],
                f"参考文献编号 [{number}] 重复出现 {count} 次",
            ))
    if seen_numbers and seen_numbers != sorted(seen_numbers):
        first = next(p for p in refs)
        issues.append(_issue(
            "REF-001", "low", first,
            "条目编号未按升序排列，请检查编号顺序",
        ))
        for e in entries:
            if e["number"] in seen_numbers and not any(
                    it["code"] == "REF-001" for it in e["problems"]):
                e["problems"].append({"code": "REF-001", "severity": "low",
                                      "message": "编号顺序与条目顺序不一致"})
                break

    # 超出条目数的引用
    known = {n for n in seen_numbers}
    if refs:
        dangling = [(n, p) for n, p in first_cite if n not in known]
        if dangling:
            number, p = dangling[0]
            issues.append(_issue(
                "REF-003", "high", p,
                f"正文引用 [{number}] 超出参考文献条数（共 {len(refs)} 条）",
                basis="引用编号应与参考文献一一对应",
            ))

    # 首次引用顺序（顺序编码制应递增）
    if numeric_style and len(first_cite) > 1:
        order = [n for n, _ in first_cite if n in known]
        if order != sorted(order):
            issues.append(_issue(
                "REF-004", "medium", first_cite[0][1],
                "正文首次引用顺序与参考文献编号不一致，建议按首次出现顺序重排",
                basis="顺序编码制应按正文首次引用顺序编号",
            ))

    counts = {"high": 0, "medium": 0, "low": 0}
    for it in issues:
        counts[it["severity"]] += 1
    score = max(0, 100 - sum(_SEVERITY_DEDUCT[s] * c for s, c in counts.items()))
    changed = sum(1 for e in entries if e["changed"])
    uncited = sum(1 for e in entries if not e["cited"])
    summary = (
        f"参考文献 {len(entries)} 条，正文引用 {len(cited_numbers)} 个编号，"
        f"发现 {len(issues)} 个问题（高 {counts['high']} / 中 {counts['medium']}"
        f" / 低 {counts['low']}），{changed} 条可自动规范化。"
        if entries else "未识别到参考文献条目。"
    )
    return {
        "score": score,
        "summary": summary,
        "reference_style": style,
        "entries": entries,
        "issues": issues,
        "stats": {
            "references": len(entries),
            "cited_numbers": len(cited_numbers),
            "uncited": uncited,
            "duplicates": sum(1 for e in entries
                              if any(it["code"] == "REF-006" for it in e["problems"])),
            "missing_number": sum(1 for e in entries if e["number"] is None),
            "changed": changed,
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
        },
    }
