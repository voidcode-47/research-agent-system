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

    # 情形 A：条目不足正文所需编号 → 无法建立完整映射，保守返回原文
    if max(cite_seq) > len(items):
        logger.warning("报告引用规范化跳过：参考文献条目数 %d < 正文最大引用编号 %d",
                       len(items), max(cite_seq))
        return md

    # 情形 B：按正文首次出现顺序重排条目
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

    used: set[int] = set()
    ordered: list[str] = []
    for new_no, old_no in enumerate(cite_seq, 1):
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

    mapping = {old_no: new_no for new_no, old_no in enumerate(cite_seq, 1)}
    new_body = _remap_body_citations(body, mapping)

    heading = m.group(0)
    new_ref = heading + "\n\n" + "\n".join(ordered) + "\n"
    return new_body + new_ref
