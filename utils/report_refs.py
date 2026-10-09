# -*- coding: utf-8 -*-
"""报告引用规范化：解决本地小模型生成的 [n] 编号与参考文献列表不一致问题。

思路（确定性文本处理，不依赖模型能力）：
1. 按正文中 [n] 首次出现顺序得到引用序列；
2. 解析文末「参考文献」条目（支持编号 / [n] / 无编号 - 三种格式）；
3. 以正文引用顺序为唯一标准，重排并重新编号参考文献条目，同步替换正文中的编号。

这样无论模型生成时编号多乱，交付的报告都是「正文 [n] ↔ 参考文献编号」一一对应的。
若无法可靠解析（无参考文献段落 / 无可用条目），原样返回，绝不破坏原文。
"""
import re

import logging

logger = logging.getLogger(__name__)

# 参考文献段落标题（## 参考文献 / ## 参考来源 / ## References 等）
_REF_HEADING_RE = re.compile(r"(?m)^\s*#+\s*(参考(文献|来源|资料)|references)\s*$", re.IGNORECASE)
# 正文引用：[1] [2] 或 [1][2] 或 [1,2] 或 [1-3]
_CITE_RE = re.compile(r"\[(\d+)\]")
# 参考文献条目格式
_ITEM_NUM_RE = re.compile(r"^\s*(\d+)\s*[\.、]\s*(.+)$")     # 1. 标题 或 1、标题
_ITEM_BRACKET_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+)$")       # [1] 标题
_ITEM_DASH_RE = re.compile(r"^\s*[-*]\s+(.+)$")               # - 标题

# 检索工具输出的条目行：
#  - 学术检索: "[1] 标题" / "    https://doi.org/..."
#  - 网页检索: "1. 标题" / "   来源: https://..."
_ITEM_BRACKET_LINE_RE = re.compile(r"^\s*\[\d+\]\s*(.+)$")
_ITEM_DOT_LINE_RE = re.compile(r"^\s*(\d+)\s*[\.、]\s*(.+)$")
_URL_RE = re.compile(r"https?://\S+")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+")
_SRC_LINE_RE = re.compile(r"^\s*来源[:：]\s*(\S+)$")


def build_reference_list(research_data: list[str]) -> str:
    """从研究员收集的原始资料文本中提取统一编号的可引用文献清单。

    研究员会同时使用多个检索工具，输出编号各自独立（学术检索用 [n]、
    网页检索用 n.），拼接后编号冲突，小模型据此生成的引用必然错乱。
    本函数把全部条目重新统一编号并去重，供总结员作为唯一的引用池。

    Args:
        research_data: 研究员收集的文本列表（工具输出原文）。

    Returns:
        形如 "[1] 标题 | 来源\n[2] 标题 | 来源..." 的清单字符串；
        提取不到任何条目时返回空字符串。
    """
    items: list[tuple[str, str]] = []  # (title, source)

    def _push(title: str, source: str) -> None:
        title = re.sub(r"\s+", " ", title or "").strip()
        source = (source or "").strip()
        if not title:
            return
        # 去重：标题前 40 字符相同，或来源（URL/DOI）相同
        for t, s in items:
            if source and s and source == s:
                return
            if title[:40] == t[:40]:
                return
        items.append((title, source))

    for chunk in research_data:
        if not chunk or not chunk.strip():
            continue
        lines = chunk.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            title = None
            # 学术检索条目行: "[1] 标题"
            bm = _ITEM_BRACKET_LINE_RE.match(line)
            if bm and re.match(r"^\[\d+\]\s", line):
                title = bm.group(1)
            else:
                # 网页检索条目行: "1. 标题" 或 "1、标题"
                dm = _ITEM_DOT_LINE_RE.match(line)
                if dm:
                    title = dm.group(2)
            if not title:
                i += 1
                continue
            # 向后扫描最多 4 行找来源（URL / DOI / 来源: xx）
            source = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                sl = lines[j].strip()
                sm = _SRC_LINE_RE.match(sl)
                if sm:
                    source = sm.group(1)
                    break
                um = _URL_RE.search(sl)
                if um:
                    source = um.group(0)
                    break
                dm = _DOI_RE.search(sl)
                if dm:
                    source = "https://doi.org/" + dm.group(0)
                    break
            _push(title, source)
            i += 1

    if not items:
        return ""
    return "\n".join(f"[{n}] {t}{' | ' + s if s else ''}" for n, (t, s) in enumerate(items, 1))


def extract_citations(body: str) -> list[int]:
    """正文中 [n] 按首次出现顺序去重。"""
    seen: list[int] = []
    for m in _CITE_RE.finditer(body):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def _parse_ref_items(ref_section: str) -> list[tuple[int | None, str]]:
    """解析参考文献条目，返回 [(原编号或 None, 内容)]。跳过空行与说明行。"""
    items: list[tuple[int | None, str]] = []
    skip_words = ("无工具调用记录", "（无工具调用记录）")
    for line in ref_section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(w in stripped for w in skip_words):
            continue
        m = _ITEM_NUM_RE.match(stripped)
        if m:
            items.append((int(m.group(1)), m.group(2).strip()))
            continue
        m = _ITEM_BRACKET_RE.match(stripped)
        if m:
            items.append((int(m.group(1)), m.group(2).strip()))
            continue
        m = _ITEM_DASH_RE.match(stripped)
        if m:
            items.append((None, m.group(1).strip()))
            continue
        # 非条目行（如引用说明文字）：跳过，不当作条目
    return items


def _drop_invalid_citations(body: str, cite_seq: list[int], valid_numbers: set[int]) -> str:
    """移除正文中『无对应参考文献条目』的 [n] 引用标记（保留周围文字）。

    小模型常编造超出清单的引用编号（如清单只有 1 条却写 [5]）。
    这些编号无法对应任何条目，保留只会让报告"引用悬空"；
    确定性移除比保留错误引用更安全，也不编造新内容。
    """
    for n in cite_seq:
        if n not in valid_numbers:
            body = re.sub(rf"\[{n}\]", "", body)
    return body


def _remap_body_citations(body: str, mapping: dict[int, int]) -> str:
    """将正文中的 [old] 替换为 [new]，保持连写 [1][2] 逐段替换。"""
    if not mapping:
        return body
    # 反向映射避免旧编号碰撞：先把旧号替换为占位，再统一替换回新号
    placeholder = {old: f"\x00CITE{n}\x00" for n, old in enumerate(sorted(mapping))}
    tmp = _CITE_RE.sub(lambda m: placeholder.get(int(m.group(1)), m.group(0)), body)
    # 恢复：占位 -> [新号]
    for old, ph in placeholder.items():
        tmp = tmp.replace(ph, f"[{mapping[old]}]")
    return tmp


def normalize_report_references(md: str) -> str:
    """规范化整篇报告的引用编号与参考文献列表。"""
    if not md:
        return md

    m = _REF_HEADING_RE.search(md)
    if not m:
        return md
    body = md[: m.start()]
    ref_section = md[m.start():]

    cite_seq = extract_citations(body)
    if not cite_seq:
        return md  # 正文无引用，无需处理

    items = _parse_ref_items(ref_section)
    if not items:
        return md  # 无可用条目，不冒险改动

    # 建立「原编号 → 内容」映射：显式编号条目用显式编号；
    # 无编号条目按其在列表中的位置隐含编号（第 1 条=1，第 2 条=2……）。
    num_to_item: dict[int, str] = {}
    implicit = 1
    for n, content in items:
        if n is not None:
            num_to_item[n] = content
        else:
            num_to_item[implicit] = content
            implicit += 1

    valid_numbers = {n for n in num_to_item}
    valid_cite_seq = [n for n in cite_seq if n in valid_numbers]

    if not valid_cite_seq:
        # 所有引用编号都无对应条目：移除全部引用标记，条目按原顺序保留
        logger.warning("报告引用规范化：正文引用全部无对应条目，移除引用标记")
        return _CITE_RE.sub("", body) + ref_section

    if len(valid_cite_seq) != len(cite_seq):
        # 部分引用编号无对应条目（模型编造，如条目 1 条却写 [5]）：
        # 确定性移除这些悬空引用，避免"正文引用但参考文献没有"
        logger.warning("报告引用规范化：移除 %d 个无对应条目的引用编号",
                       len(cite_seq) - len(valid_cite_seq))
        body = _drop_invalid_citations(body, cite_seq, valid_numbers)

    # 按正文首次出现顺序重排条目（仅有效引用）
    used: set[int] = set()
    ordered: list[str] = []
    for new_no, old_no in enumerate(valid_cite_seq, 1):
        if old_no in num_to_item and old_no not in used:
            ordered.append(f"{new_no}. {num_to_item[old_no]}")
            used.add(old_no)
        # 该引用无对应条目（模型编造）：跳过该引用编号，不生成占位，避免编造内容

    # 正文未引用的条目按原顺序追加到末尾（保信息不丢）
    tail: list[str] = []
    counter = len(ordered)
    for n in num_to_item:
        if n not in used:
            counter += 1
            tail.append(f"{counter}. {num_to_item[n]}")
    ordered.extend(tail)

    mapping = {old_no: new_no for new_no, old_no in enumerate(valid_cite_seq, 1)}
    new_body = _remap_body_citations(body, mapping)

    heading = m.group(0)
    new_ref = heading + "\n\n" + "\n".join(ordered) + "\n"
    return new_body + new_ref
