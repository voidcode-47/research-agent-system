"""递归字符切分器。

按 \n\n → \n → 。 → 空格 的顺序递归切分，
保留语义边界。
"""
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# 中文和英文的分隔符优先级
_DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", " ", ""]


class TextSplitter:
    """递归字符切分。"""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: Optional[list[str]] = None,
    ):
        """初始化。

        Args:
            chunk_size: 每块最大字符数
            chunk_overlap: 块间重叠字符数
            separators: 切分符优先级列表
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or _DEFAULT_SEPARATORS

    def split(self, text: str) -> list[str]:
        """切分文本。"""
        if not text or not text.strip():
            return []

        if len(text) <= self.chunk_size:
            return [text.strip()]

        return self._split_text(text, self.separators)

    def split_documents(self, documents: list[dict]) -> list[dict]:
        """切分文档列表。

        Args:
            documents: [{"content": str, "metadata": dict}]

        Returns:
            [{"content": str, "metadata": dict}] 切分后的文档
        """
        result = []
        for doc in documents:
            chunks = self.split(doc["content"])
            for i, chunk in enumerate(chunks):
                meta = dict(doc.get("metadata", {}))
                meta["chunk_index"] = i
                result.append({
                    "content": chunk,
                    "metadata": meta,
                })
        logger.info(f"切分完成: {len(documents)} 文档 → {len(result)} 块")
        return result

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """递归切分。"""
        if len(text) <= self.chunk_size:
            return [text.strip()] if text.strip() else []

        # 找到第一个在文本中存在的分隔符
        separator = ""
        new_separators = []
        for i, sep in enumerate(separators):
            if sep == "":
                separator = ""
                new_separators = []
                break
            if sep in text:
                separator = sep
                new_separators = separators[i + 1:]
                break

        # 用分隔符切分
        if separator:
            splits = text.split(separator)
        else:
            # 没有分隔符，按字符数硬切
            splits = self._hard_split(text)

        # 合并小块
        chunks = []
        current = ""
        for split in splits:
            piece = split if not separator else split + separator

            if len(current) + len(piece) > self.chunk_size and current:
                chunks.append(current.strip())
                # 保留重叠
                overlap_text = current[-self.chunk_overlap:] if self.chunk_overlap > 0 else ""
                current = overlap_text + piece
            else:
                current += piece

        if current.strip():
            chunks.append(current.strip())

        # 如果还需要更细切分
        final_chunks = []
        for chunk in chunks:
            if len(chunk) > self.chunk_size and new_separators:
                final_chunks.extend(self._split_text(chunk, new_separators))
            elif chunk.strip():
                final_chunks.append(chunk)

        return final_chunks

    def _hard_split(self, text: str) -> list[str]:
        """硬切分（无分隔符时）。"""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunks.append(text[i:i + self.chunk_size])
        return chunks
