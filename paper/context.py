"""Bounded, section-balanced evidence samples with explicit coverage metadata."""
import re

_KEY_SECTION = re.compile(r"问题|方法|数据|实验|结果|结论|question|method|data|experiment|result|conclusion", re.IGNORECASE)
_EXCLUDED = {"heading", "caption", "cover", "toc", "table", "table_note", "reference"}


def sample_context(parsed: dict, max_chars: int = 6000) -> tuple[str, dict]:
    max_chars = max(0, int(max_chars))
    groups = {}
    for paragraph in parsed.get("paragraphs") or []:
        if paragraph.get("type") in _EXCLUDED or paragraph.get("table_id"):
            continue
        if not (paragraph.get("text") or "").strip():
            continue
        section = str(paragraph.get("section") or "正文")
        groups.setdefault(section, []).append(paragraph)
    names = sorted(groups, key=lambda name: not bool(_KEY_SECTION.search(name)))
    # Round-robin sections; within each section include both ends before its middle.
    ordered = {}
    for name, items in groups.items():
        indices = []
        for lo in range((len(items) + 1) // 2):
            indices.append(lo)
            hi = len(items) - 1 - lo
            if hi != lo:
                indices.append(hi)
        ordered[name] = [items[i] for i in indices]
    header = (
        f"标题：{str(parsed.get('title') or '（未识别）')[:160]}\n"
        f"摘要：{str(parsed.get('abstract') or '')[:600]}\n"
        f"关键词：{'；'.join(str(k) for k in (parsed.get('keywords') or [])[:8])[:240]}\n"
        "以下为按章节抽样的证据；未覆盖内容不能视为不存在，信息不足须明确说明。\n正文节选："
    )[:max_chars // 3]
    lines = [header]
    remaining = max_chars - len(header)
    sampled = []
    sampled_sections = []
    sampled_chars = 0
    depth = 0
    active = list(names)
    while active and remaining > 60:
        next_active = []
        for index, name in enumerate(active):
            paragraph = ordered[name][depth]
            prefix = f"[{paragraph.get('id')} 第{paragraph.get('page') or 0}页 {name[:60]}] "
            allowance = min(1000, remaining // max(1, len(active) - index) - len(prefix) - 1)
            if allowance <= 0:
                continue
            text = paragraph["text"][:allowance]
            line = "\n" + prefix + text
            lines.append(line)
            remaining -= len(line)
            sampled_chars += len(text)
            sampled.append(paragraph.get("id"))
            if name not in sampled_sections:
                sampled_sections.append(name)
            if len(ordered[name]) > depth + 1:
                next_active.append(name)
        active = next_active
        depth += 1
    total_chars = sum(len(p["text"]) for items in groups.values() for p in items)
    return "".join(lines), {
        "paragraph_ids": sampled, "sections": sampled_sections,
        "omitted_sections": [name for name in names if name not in sampled_sections],
        "sampled_paragraphs": len(sampled),
        "total_paragraphs": sum(len(items) for items in groups.values()),
        "sampled_chars": sampled_chars, "total_chars": total_chars,
        "partial": sampled_chars < total_chars,
    }
