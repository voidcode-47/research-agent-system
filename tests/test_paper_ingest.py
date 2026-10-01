# -*- coding: utf-8 -*-
"""论文入库工具测试（mock 网络/向量库）：DOI 提取、非 OA 提示、完整链路、超限。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.paper_ingest import PaperIngestTool


def _vs_mock():
    vs = MagicMock()
    vs.add_documents.return_value = 5
    return vs


class TestPaperIngest:
    def test_extract_doi(self):
        t = PaperIngestTool(_vs_mock())
        assert t._extract_doi("https://doi.org/10.1371/journal.pone.0252573") == "10.1371/journal.pone.0252573"
        assert t._extract_doi("10.3390/s18082674") == "10.3390/s18082674"
        assert t._extract_doi("无 DOI 文本") == ""

    def test_invalid_input(self):
        t = PaperIngestTool(_vs_mock())
        out = t.execute("这不是 DOI")
        assert "无法从输入中识别 DOI" in out

    def test_metadata_failure(self):
        t = PaperIngestTool(_vs_mock())
        with patch.object(t, "_fetch_metadata", side_effect=RuntimeError("网络")):
            out = t.execute("10.1000/xyz")
        assert "无法查询论文元数据" in out

    def test_no_oa_pdf_hint(self):
        t = PaperIngestTool(_vs_mock())
        with patch.object(t, "_fetch_metadata", return_value={"title": "论文A", "year": 2024, "journal": "J", "pdf_url": "", "doi": "10.1000/xyz"}), \
             patch.object(t, "_find_oa_pdf", return_value=""):
            out = t.execute("10.1000/xyz")
        assert "无开放获取全文" in out

    def test_download_failure(self):
        t = PaperIngestTool(_vs_mock())
        with patch.object(t, "_fetch_metadata", return_value={"title": "A", "year": 2024, "journal": "J", "pdf_url": "", "doi": "10.1000/xyz"}), \
             patch.object(t, "_find_oa_pdf", return_value="https://pdf.example/a.pdf"), \
             patch.object(t, "_download_pdf", side_effect=RuntimeError("403")):
            out = t.execute("10.1000/xyz")
        assert "PDF 下载失败" in out and "403" in out

    def test_download_too_large(self):
        t = PaperIngestTool(_vs_mock())
        with patch.object(t, "_fetch_metadata", return_value={"title": "A", "year": 2024, "journal": "J", "pdf_url": "", "doi": "10.1000/xyz"}), \
             patch.object(t, "_find_oa_pdf", return_value="https://pdf.example/a.pdf"), \
             patch.object(t, "_download_pdf", return_value=False):
            out = t.execute("10.1000/xyz")
        assert "超过 30MB 限制" in out

    def test_full_ingest_flow(self, tmp_path):
        vs = _vs_mock()
        vs.add_documents.return_value = 2  # 与 2 个解析片段一致
        t = PaperIngestTool(vs)
        with patch.object(t, "_fetch_metadata", return_value={"title": "论文A", "year": 2024, "journal": "期刊B", "pdf_url": "", "doi": "10.1000/xyz"}), \
             patch.object(t, "_find_oa_pdf", return_value="https://pdf.example/a.pdf"), \
             patch.object(t, "_download_pdf", return_value=True), \
             patch("tools.pdf_parser.PDFParserTool") as MockParser:
            MockParser.return_value.parse_file.return_value = [
                {"content": "段落1", "metadata": {}},
                {"content": "段落2", "metadata": {}},
            ]
            out = t.execute("10.1000/xyz")
        assert "已入库论文《论文A》" in out
        assert "期刊B · 2024 · 2 个片段" in out
        # 向量库收到的文档带论文元数据
        args = vs.add_documents.call_args
        assert args.kwargs["collection_name"] == "papers"
        assert args.kwargs["documents"][0]["metadata"]["kind"] == "paper"
        assert args.kwargs["documents"][0]["metadata"]["title"] == "论文A"
