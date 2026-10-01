# -*- coding: utf-8 -*-
"""Markdown → Word 轻量转换器。

支持研究报告常用的语法子集：标题(#/##/###)、无序列表(-)、引用(>)、
分隔线(---)、粗体(**text**)、行内链接([text](url))。其余内容按段落保留原文。
"""
from io import BytesIO

import re

from utils.logger import get_logger

logger = get_logger(__name__)

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _add_runs(paragraph, text: str):
    """把含 **粗体** 和 [链接](url) 的文本写入段落。"""
    # 先处理行内链接：转成 "text (url)"，粗体再单独处理
    def link_sub(m):
        return f"{m.group(1)} ({m.group(2)})"

    text = _LINK_RE.sub(link_sub, text)

    # 按粗体拆分
    parts = _BOLD_RE.split(text)
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            run = paragraph.add_run(part)
            run.bold = True
        else:
            paragraph.add_run(part)


def md_to_docx(markdown_text: str) -> BytesIO:
    """把 Markdown 文本转成 .docx 字节流。

    Returns:
        BytesIO 内存中的 docx 文件
    """
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    # 默认中文字体
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)
    try:
        style.element.rPr.rFonts.set(
            _docx_oxml_ns_qn("w:eastAsia"), "Microsoft YaHei"
        )
    except Exception:
        pass

    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        # 分隔线
        if line.strip() in ("---", "***", "___"):
            doc.add_paragraph("─" * 40)
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            heading = doc.add_heading(level=min(level, 4))
            _add_runs(heading, text)
            i += 1
            continue

        # 引用
        if line.lstrip().startswith(">"):
            quote = line.lstrip()[1:].strip()
            p = doc.add_paragraph()
            run = p.add_run(quote)
            run.italic = True
            i += 1
            continue

        # 无序列表
        m = re.match(r"^\s*[-*•]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            _add_runs(p, m.group(1).strip())
            i += 1
            continue

        # 有序列表（1. 2.）
        m = re.match(r"^\s*(\d+)[.、]\s+(.*)$", line)
        if m:
            p = doc.add_paragraph(style="List Number")
            _add_runs(p, m.group(2).strip())
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        _add_runs(p, line)
        i += 1

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _docx_oxml_ns_qn(name: str):
    """docx oxml 命名空间（延迟导入，避免未安装时模块不可用）。"""
    from docx.oxml.ns import qn
    return qn(name)
