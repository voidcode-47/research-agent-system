# -*- coding: utf-8 -*-
"""学术检索工具测试（mock 网络）：Crossref / OpenAlex 解析、被引/OA 标记、失败上报。"""
from unittest.mock import MagicMock, patch

from tools.academic_search import AcademicSearchTool, _rebuild_abstract


def _crossref_json(items):
    return {"message": {"items": items}}


def _openalex_json(works):
    return {"meta": {"count": len(works)}, "results": works}


def _router(crossref_items, openalex_works):
    """按 URL 分派两个来源的假响应（execute 会依次请求 Crossref 与 OpenAlex）。"""
    def _get(url, **kwargs):
        payload = (_crossref_json(crossref_items) if "crossref" in url
                   else _openalex_json(openalex_works))
        return MagicMock(raise_for_status=lambda: None, json=lambda: payload, status_code=200)
    return _get


def _work(title="基于深度学习的图像识别", doi="10.1000/cn1", cited=3, abstract=None):
    """OpenAlex 单片记录。"""
    return {
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "display_name": title,
        "publication_year": 2024,
        "language": "zh",
        "cited_by_count": cited,
        "authorships": [{"author": {"display_name": "张 三"}},
                        {"author": {"display_name": "李 四"}}],
        "primary_location": {
            "source": {"display_name": "教育研究"},
            "landing_page_url": f"https://doi.org/{doi}",
        },
        "best_oa_location": {"pdf_url": f"https://oa.example/{doi}.pdf"},
        "abstract_inverted_index": abstract,
    }


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

    def test_crossref_null_fields_do_not_break_parsing(self):
        """Crossref 对无作者/无摘要/无日期的记录返回 null，不能因此丢掉整段结果。"""
        item = _item()
        item["author"] = None
        item["abstract"] = None
        item["published-print"] = None
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                raise_for_status=lambda: None,
                json=lambda: _crossref_json([item]),
            )
            out = AcademicSearchTool().execute("深度学习", max_results=5)
        assert "深度学习综述" in out

    def test_crossref_null_author_entries_skipped(self):
        item = _item()
        item["author"] = [None, {"family": "张", "given": "三"}]
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                raise_for_status=lambda: None,
                json=lambda: _crossref_json([item]),
            )
            out = AcademicSearchTool().execute("深度学习", max_results=5)
        assert "张 三" in out

    def test_broken_source_is_reported_not_silently_dropped(self):
        """旧实现静默吞掉异常：中文文献一节直接消失，看不出任何异常。"""
        def _get(url, **kwargs):
            if "crossref" in url:
                return MagicMock(
                    raise_for_status=lambda: None,
                    json=lambda: _crossref_json([_item()]),
                )
            raise RuntimeError("connection reset")

        with patch("requests.get", side_effect=_get):
            out = AcademicSearchTool().execute("q", max_results=2)

        assert "【国际期刊文献 (Crossref)】" in out   # 好的来源照常返回
        assert "失败" in out                          # 坏的来源必须被点出来
        assert "部分来源未返回结果" in out

    def test_all_sources_failing_reports_failure(self):
        with patch("requests.get", side_effect=RuntimeError("网络失败")):
            out = AcademicSearchTool().execute("q")
        assert "学术文献检索失败" in out


class TestOpenAlexSource:
    def test_chinese_literature_section(self):
        with patch("requests.get", side_effect=_router([], [_work()])):
            out = AcademicSearchTool().execute("图像识别", max_results=3)

        assert "OpenAlex" in out
        assert "基于深度学习的图像识别" in out
        assert "教育研究" in out            # 中文期刊名
        assert "https://doi.org/10.1000/cn1" in out
        assert "📄开放全文" in out
        assert "被引 3 次" in out

    def test_openalex_null_abstract_and_missing_source(self):
        """无摘要、无来源期刊的记录不能导致整段解析失败。"""
        works = [_work(abstract=None)]
        works[0]["primary_location"] = {}
        works[0]["best_oa_location"] = None
        with patch("requests.get", side_effect=_router([], works)):
            out = AcademicSearchTool().execute("图像识别", max_results=3)

        assert "基于深度学习的图像识别" in out
        assert "该库未提供摘要" in out

    def test_empty_openalex_result_is_reported(self):
        with patch("requests.get", side_effect=_router([_item()], [])):
            out = AcademicSearchTool().execute("q", max_results=2)

        assert "OpenAlex" in out and "无结果" in out

    def test_openalex_request_asks_for_chinese_and_uses_polite_pool(self):
        seen = {}

        def _get(url, **kwargs):
            if "openalex" in url:
                seen.update(kwargs.get("params") or {})
                return MagicMock(
                    raise_for_status=lambda: None,
                    json=lambda: _openalex_json([_work()]),
                    status_code=200,
                )
            return MagicMock(
                raise_for_status=lambda: None,
                json=lambda: _crossref_json([]),
                status_code=200,
            )

        with patch("requests.get", side_effect=_get):
            AcademicSearchTool().execute("图像识别", max_results=7)

        assert seen.get("filter") == "language:zh"
        assert seen.get("per-page") == 7
        assert seen.get("mailto")            # 礼貌池邮箱，提高配额
        assert "abstract_inverted_index" in (seen.get("select") or "")


class TestAbstractRebuild:
    def test_chinese_abstract_has_no_stray_spaces(self):
        inverted = {"图像识别": [0], "技术": [1], "应用广泛": [2]}
        assert _rebuild_abstract(inverted) == "图像识别技术应用广泛"

    def test_english_abstract_keeps_word_separators(self):
        inverted = {"Deep": [0], "learning": [1], "works": [2]}
        assert _rebuild_abstract(inverted) == "Deep learning works"

    def test_missing_abstract_returns_empty(self):
        assert _rebuild_abstract(None) == ""
        assert _rebuild_abstract({}) == ""
