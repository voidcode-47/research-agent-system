# -*- coding: utf-8 -*-
"""学术检索工具测试（mock 网络）：Crossref 解析、被引/OA 标记、失败降级。"""
from unittest.mock import MagicMock, patch

from tools.academic_search import AcademicSearchTool


def _crossref_json(items):
    return {"message": {"items": items}}


def _item(title="深度学习综述", doi="10.1000/xyz", cited=12, abstract="<p>摘要文本</p>", oa=False):
    it = {
        "title": [title],
        "author": [{"family": "张", "given": "三"}, {"family": "李", "given": "四"}],
        "container-title": ["计算机学报"],
        "published-print": {"date-parts": [[2024]]},
        "abstract": abstract,
        "DOI": doi,
        "URL": f"https://doi.org/{doi}",
        "is-referenced-by-count": cited,
        "link": [],
    }
    if oa:
        it["link"] = [{"content-type": "application/pdf", "URL": f"https://oa.example/{doi}.pdf"}]
    return it


class TestAcademicSearch:
    def test_crossref_cited_and_oa_mark(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                raise_for_status=lambda: None,
                json=lambda: _crossref_json([
                    _item(cited=12),
                    _item(title="开放论文", doi="10.2000/abc", cited=0, oa=True),
                ]),
            )
            out = AcademicSearchTool().execute("深度学习", max_results=5)
        assert "【国际期刊文献 (Crossref)】" in out
        assert "被引 12 次" in out
        assert "新近论文" in out          # 被引 0 → 新近论文
        assert "📄开放全文" in out        # OA 标记
        assert "https://doi.org/10.2000/abc" in out
        assert "张 三, 李 四" in out
        assert "计算机学报" in out

    def test_abstract_html_stripped_and_truncated(self):
        with patch("requests.get") as mock_get:
            long_abs = "<p>" + "长摘要内容。" * 200 + "</p>"
            mock_get.return_value = MagicMock(
                raise_for_status=lambda: None,
                json=lambda: _crossref_json([_item(abstract=long_abs)]),
            )
            out = AcademicSearchTool().execute("q", max_results=1)
        assert "<p>" not in out
        assert len([l for l in out.splitlines() if "长摘要" in l][0]) <= 400 + 10

    def test_crossref_failure_still_returns_message(self):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = RuntimeError("网络失败")
            out = AcademicSearchTool().execute("q")
        assert "学术文献检索失败" in out or "q" in out

    def test_cn_site_parse(self):
        page = """
        <html><body>
        <div class="result">
          <h3><a href="http://wanfang/cnki-link">中文论文标题</a></h3>
          <span>摘要片段内容</span>
        </div>
        </body></html>
        """
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                raise_for_status=lambda: None,
                text=page,
            )
            out = AcademicSearchTool().execute("q", max_results=3)
        assert "中文论文标题" in out
        assert "中文文献" in out
