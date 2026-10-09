"""论文入库工具：将学术论文（DOI）下载入库到向量库，供全文问答。

流程：解析 DOI → Crossref 查元数据与开放获取(OA)全文链接 → 下载 PDF →
PyMuPDF 解析 → 分块入库 ChromaDB（独立集合「papers」）。
借鉴 Paper-Agent 的"渐进式阅读"：检索到题录后进一步获取全文，
形成「检索 → 入库 → 精读问答」闭环。
"""
import re
from pathlib import Path
from urllib.parse import quote

from tools.base import BaseTool
from tools.vector_store import VectorStore
from utils.logger import get_logger

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_MAILTO = "research-assistant@users.noreply.github.com"
_MAX_PDF_BYTES = 30 * 1024 * 1024  # 30MB 上限


class PaperIngestTool(BaseTool):
    """论文入库：DOI → 下载 OA 全文 → 解析 → 写入向量库。"""

    name = "paper_ingest"
    description = (
        "将一篇学术论文下载入库到知识库（需该论文有开放获取全文）。"
        "输入论文的 DOI（如 10.1000/xyz123）或含 DOI 的链接。"
        "入库后可针对全文进行问答。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "doi_or_url": {
                "type": "string",
                "description": "论文 DOI 或含 DOI 的链接",
            },
        },
        "required": ["doi_or_url"],
    }

    def __init__(self, vector_store: VectorStore, collection: str = "papers"):
        self._vs = vector_store
        self._collection = collection

    def _extract_doi(self, text: str) -> str:
        """从 DOI 或 URL 中提取 DOI。"""
        m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", text)
        return m.group(0).rstrip(".,;") if m else ""

    def _fetch_metadata(self, doi: str) -> dict:
        """Crossref 查询论文元数据与 OA 全文链接。"""
        import requests

        r = requests.get(
            f"https://api.crossref.org/works/{quote(doi)}",
            params={"mailto": _MAILTO},
            headers={"User-Agent": f"research-assistant/1.0 (mailto:{_MAILTO})"},
            timeout=15,
        )
        r.raise_for_status()
        msg = r.json()["message"]
        title = (msg.get("title") or [""])[0].strip()
        year = None
        for k in ("published-print", "published-online", "published", "issued"):
            dp = (msg.get(k) or {}).get("date-parts")
            if dp and dp[0]:
                year = dp[0][0]
                break
        journal = (msg.get("container-title") or [""])[0].strip()
        # 备选：Crossref link 字段（部分出版商标注了 PDF 链接）
        pdf_url = ""
        for link in msg.get("link") or []:
            if isinstance(link, dict) and link.get("content-type") == "application/pdf":
                pdf_url = link.get("URL", "")
                break
        return {
            "title": title,
            "year": year,
            "journal": journal,
            "doi": doi,
            "pdf_url": pdf_url,
        }

    def _find_oa_pdf(self, doi: str) -> str:
        """Unpaywall 发现 OA PDF 直链（优先），失败回退 Crossref 链接。"""
        import requests

        # 1) Unpaywall（覆盖 MDPI/PLOS/arXiv 等 OA 来源，按 DOI 返回最优 PDF 直链）
        try:
            r = requests.get(
                f"https://api.unpaywall.org/v2/{quote(doi)}",
                params={"email": _MAILTO},
                timeout=15,
            )
            r.raise_for_status()
            loc = r.json().get("best_oa_location") or {}
            pdf = (loc.get("url_for_pdf") or "").strip()
            if pdf:
                return pdf
        except Exception as e:
            logger.debug(f"Unpaywall 查询失败 {doi}: {e}")

        # 2) 回退 Crossref link 字段
        try:
            meta = self._fetch_metadata(doi)
            if meta.get("pdf_url"):
                return meta["pdf_url"]
        except Exception as e:
            logger.debug(f"Crossref 回退失败 {doi}: {e}")

        return ""

    def _download_pdf(self, url: str, dest: Path) -> bool:
        """下载 PDF 到本地，带浏览器 UA，限制大小。"""
        import requests

        with requests.get(
            url,
            headers={
                "User-Agent": _UA,
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.5",
            },
            timeout=30,
            stream=True,
        ) as r:
            r.raise_for_status()
            size = 0
            too_large = False
            try:
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        f.write(chunk)
                        size += len(chunk)
                        if size > _MAX_PDF_BYTES:
                            too_large = True
                            break
            except Exception:
                # 写入失败同样清理，避免留下半个文件占位
                dest.unlink(missing_ok=True)
                raise
            if too_large:
                # 必须在文件关闭后再删：Windows 上删除仍被占用的文件会报错
                dest.unlink(missing_ok=True)
                return False
        return size > 1024

    def execute(self, doi_or_url: str) -> str:
        """执行论文入库。"""
        doi = self._extract_doi(doi_or_url)
        if not doi:
            return (
                f"无法从输入中识别 DOI: {doi_or_url}\n"
                "请提供论文 DOI（如 10.1000/xyz123）或含 DOI 的链接。"
            )

        # 1) 元数据
        try:
            meta = self._fetch_metadata(doi)
        except Exception as e:
            logger.warning(f"Crossref 元数据查询失败 {doi}: {e}")
            return f"无法查询论文元数据（{doi}），请检查 DOI 是否正确。"

        # 2) 发现 OA PDF（Unpaywall 优先）
        pdf_url = self._find_oa_pdf(doi)
        if not pdf_url:
            return (
                f"论文《{meta['title']}》（{meta['year'] or '年份未知'}）无开放获取全文，"
                "无法下载入库。\n可在 https://doi.org/{doi} 查看题录与访问方式。"
            )

        # 2) 下载 PDF
        safe_name = re.sub(r"[^\w.-]", "_", doi)[:80]
        pdf_dir = Path("data/papers")
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"{safe_name}.pdf"

        try:
            ok = self._download_pdf(pdf_url, pdf_path)
            if not ok:
                return f"PDF 下载失败或超过 30MB 限制: {pdf_url}"
        except Exception as e:
            logger.warning(f"PDF 下载失败 {pdf_url}: {e}")
            return f"PDF 下载失败（网络或权限）: {e}\n来源: {pdf_url}"

        # 3) 解析 PDF
        from tools.pdf_parser import PDFParserTool
        documents = PDFParserTool().parse_file(str(pdf_path))
        if not documents:
            return f"PDF 解析未提取到文本（可能是扫描件）: {pdf_path}"

        # 4) 入库（metadata 带论文信息，供溯源）
        for i, doc in enumerate(documents):
            doc["metadata"].update({
                "title": meta["title"],
                "doi": doi,
                "year": meta["year"] or "",
                "journal": meta["journal"] or "",
                "kind": "paper",
            })
        try:
            ids = [f"paper-{safe_name}-p{i+1}" for i in range(len(documents))]
            added = self._vs.add_documents(
                collection_name=self._collection,
                documents=documents,
                ids=ids,
            )
        except Exception as e:
            logger.error(f"论文入库失败 {doi}: {e}")
            return f"论文入库失败（向量库错误）: {e}"

        return (
            f"✅ 已入库论文《{meta['title']}》\n"
            f"   {meta['journal'] or '期刊未知'} · {meta['year'] or '年份未知'} · "
            f"{added} 个片段 → 知识库「{self._collection}」集合\n"
            f"   DOI: https://doi.org/{doi}\n"
            f"   本地 PDF: {pdf_path}\n\n"
            "提示：可在「知识库」页选择 papers 集合进行全文问答。"
        )
