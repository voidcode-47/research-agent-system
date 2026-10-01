"""统一文档加载入口，按文件扩展名分流。"""
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class DocumentLoader:
    """统一文档加载器。

    根据文件扩展名分流到 PDF/网页/Markdown/TXT 解析器。
    输出统一的 Document 对象（content + metadata）。
    """

    def load_file(self, file_path: str) -> list[dict]:
        """加载本地文件。

        Returns:
            [{"content": str, "metadata": {"source": str, "page": int}}]
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"文件不存在: {file_path}")
            return []

        ext = path.suffix.lower()
        if ext == ".pdf":
            return self._load_pdf(path)
        elif ext in (".txt", ".md", ".markdown"):
            return self._load_text(path, ext)
        elif ext in (".html", ".htm"):
            return self._load_html(path)
        else:
            logger.warning(f"不支持的文件类型: {ext}，尝试按文本加载")
            return self._load_text(path, ext)

    def load_url(self, url: str, max_length: int = 10000) -> list[dict]:
        """加载网页 URL。"""
        try:
            import trafilatura
            from trafilatura import fetch_url, extract

            html = fetch_url(url)
            if not html:
                return []

            text = extract(html, include_comments=False, include_tables=True)
            metadata = trafilatura.extract_metadata(html)
            title = metadata.title if metadata else url

            if text and len(text) > max_length:
                text = text[:max_length]

            return [{
                "content": text or "",
                "metadata": {"source": url, "title": title},
            }]
        except Exception as e:
            logger.error(f"网页加载失败 {url}: {e}")
            return []

    def _load_pdf(self, path: Path) -> list[dict]:
        """加载 PDF。"""
        try:
            import fitz
            doc = fitz.open(path)
            documents = []
            for i in range(len(doc)):
                text = doc[i].get_text("text").strip()
                if text:
                    documents.append({
                        "content": text,
                        "metadata": {"page": i + 1, "source": path.name},
                    })
            doc.close()
            logger.info(f"PDF {path.name} 加载 {len(documents)} 页")
            return documents
        except ImportError:
            logger.error("PyMuPDF 未安装")
            return []
        except Exception as e:
            logger.error(f"PDF 加载失败: {e}")
            return []

    def _load_text(self, path: Path, ext: str) -> list[dict]:
        """加载文本/Markdown。"""
        try:
            # 尝试不同编码
            for encoding in ["utf-8", "gbk", "latin-1"]:
                try:
                    content = path.read_text(encoding=encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                content = ""

            return [{
                "content": content,
                "metadata": {"source": path.name, "type": ext},
            }]
        except Exception as e:
            logger.error(f"文本加载失败: {e}")
            return []

    def _load_html(self, path: Path) -> list[dict]:
        """加载本地 HTML。"""
        try:
            import trafilatura
            html = path.read_text(encoding="utf-8", errors="ignore")
            text = trafilatura.extract(html, include_comments=False)
            return [{
                "content": text or "",
                "metadata": {"source": path.name},
            }]
        except Exception as e:
            logger.error(f"HTML 加载失败: {e}")
            return []
