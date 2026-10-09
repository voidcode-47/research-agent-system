"""PDF 解析工具，用 PyMuPDF (fitz) 提取文本，中文友好。"""
from pathlib import Path
from typing import Optional

from tools.base import BaseTool, register_tool
from utils.logger import get_logger

logger = get_logger(__name__)


class PDFParserTool(BaseTool):
    """PDF 文本提取。"""

    name = "pdf_parser"
    description = "解析 PDF 文件，提取文本内容。输入 PDF 文件路径，返回每页文本。"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "PDF 文件的本地路径",
            },
            "max_pages": {
                "type": "integer",
                "description": "最大解析页数，默认 0 表示全部",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, max_pages: int = 0) -> str:
        """解析 PDF。"""
        path = Path(file_path)
        if not path.exists():
            return f"文件不存在: {file_path}"

        try:
            import fitz  # PyMuPDF
        except ImportError:
            return "PyMuPDF 未安装，请运行 pip install PyMuPDF"

        try:
            with fitz.open(path) as doc:
                total_pages = len(doc)
                pages_to_read = total_pages if max_pages <= 0 else min(max_pages, total_pages)

                pages = []
                for i in range(pages_to_read):
                    page = doc[i]
                    text = page.get_text("text")
                    if text.strip():
                        pages.append(f"--- 第 {i+1} 页 ---\n{text.strip()}")

            if not pages:
                return "PDF 未提取到文本（可能是扫描件）"

            result = f"PDF: {path.name}（共 {total_pages} 页，已解析 {pages_to_read} 页）\n\n"
            result += "\n\n".join(pages)
            return result

        except Exception as e:
            logger.error(f"PDF 解析失败 {file_path}: {e}")
            return f"PDF 解析失败: {e}"

    def parse_file(self, file_path: str) -> list[dict]:
        """解析 PDF 为 Document 列表（供 RAG 使用）。

        Returns:
            [{"content": str, "metadata": {"page": int, "source": str}}]
        """
        path = Path(file_path)
        if not path.exists():
            return []

        try:
            import fitz
        except ImportError:
            logger.error("PyMuPDF 未安装")
            return []

        documents = []
        try:
            with fitz.open(path) as doc:
                for i in range(len(doc)):
                    text = doc[i].get_text("text").strip()
                    if text:
                        documents.append({
                            "content": text,
                            "metadata": {"page": i + 1, "source": path.name},
                        })
        except Exception as e:
            logger.error(f"PDF 解析失败: {e}")
        return documents


register_tool(PDFParserTool())
