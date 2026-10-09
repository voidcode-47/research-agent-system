"""学术文献检索工具。

来源策略（均免费、无需 Key、接口稳定）：
1. Crossref API —— 国际期刊文献（含部分中文学术期刊），返回标题/作者/年份/
   期刊/摘要/DOI 链接。
2. OpenAlex API —— 开放学术图谱，用 filter=language:zh 检索中文文献，
   覆盖（教育研究）（电子通信与计算机科学）等中文学术期刊。

历史说明：中文文献曾用"百度/必应 site:wanfangdata.com.cn"抓取，实测已失效——
搜索引擎对程序化 site: 查询返回验证码页或无关兜底结果（0 命中），
且异常被外层静默吞掉，表现为"中文文献"一节直接消失。故改为 OpenAlex。

说明：学术全文受版权与平台权限限制，本工具提供题录+摘要，足以支撑研究性
问题的文献调研；如需全文可在结果链接处访问（知网/万方部分需机构权限）。
"""
import re

from tools.base import BaseTool, register_tool
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

# 两个元数据接口的礼貌池邮箱（提供后配额更高）
_MAILTO = "research-assistant@users.noreply.github.com"
_UA = f"research-assistant/1.0 (mailto:{_MAILTO})"
_OPENALEX_API = "https://api.openalex.org/works"


def _rebuild_abstract(inverted: dict | None, limit: int = 400) -> str:
    """还原 OpenAlex 的摘要。

    OpenAlex 用 {词: [出现位置]} 的倒排索引表示摘要，需按位置拼回文本。
    中文分词后词与词之间本无空格，按空格拼接会插进大量多余空格，
    故按 CJK 字符占比选择连接符。
    """
    if not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs or []:
            positions[i] = word
    if not positions:
        return ""
    words = [positions[i] for i in sorted(positions)]
    joined = "".join(words)
    cjk = sum(1 for ch in joined if "一" <= ch <= "鿿")
    text = ("" if cjk > len(joined) * 0.3 else " ").join(words)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _format_item(index: int, it: dict) -> str:
    """统一的条目渲染（两个来源字段结构一致）。"""
    meta = " | ".join(x for x in [
        it.get("authors") or "",
        str(it["year"]) if it.get("year") else "",
        it.get("journal") or "",
    ] if x)
    cited = f"被引 {it['cited_by']} 次" if it.get("cited_by") else "新近论文"
    oa_mark = " 📄开放全文" if it.get("oa_pdf") else ""
    return (
        f"[{index}] {it['title']}{oa_mark}\n"
        f"    {meta} | {cited}\n"
        f"    {it.get('abstract') or '（该库未提供摘要）'}\n"
        f"    {it.get('url') or ''}"
    )


class AcademicSearchTool(BaseTool):
    """学术文献检索：Crossref 国际期刊 + OpenAlex 中文期刊。"""

    name = "academic_search"
    description = (
        "学术文献检索（论文），返回论文标题、作者、年份、期刊/来源、摘要与链接。"
        "国际文献来自 Crossref，中文文献来自 OpenAlex（language=中文），"
        "两者均免费、无需 Key。适合研究性问题的文献调研。"
        "优先选择与主题相关的期刊论文，书籍、教程类文献优先级低。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索主题，如「大语言模型 教育应用」",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回条数，默认 5",
            },
        },
        "required": ["query"],
    }

    def _search_crossref(self, query: str, max_results: int) -> list[dict]:
        """Crossref API：国际期刊论文元数据（mailto 进入礼貌池，降低 429）。

        返回含被引次数（is-referenced-by-count，用于识别重点论文）与
        开放获取全文链接（link 字段，供 paper_ingest 下载入库）。
        """
        import requests

        r = requests.get(
            "https://api.crossref.org/works",
            params={
                "query": query,
                "rows": max_results,
                "select": "title,author,container-title,published,abstract,DOI,URL,"
                          "is-referenced-by-count,link",
                # Crossref 礼貌池要求：提供联系邮箱以获得更高配额
                "mailto": _MAILTO,
            },
            headers={"User-Agent": _UA},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()["message"]["items"]

        out = []
        for it in items:
            title = (it.get("title") or [""])[0].strip()
            if not title:
                continue
            authors = it.get("author") or []
            parts = []
            for a in authors[:4]:
                if not isinstance(a, dict):
                    continue
                name = f"{a.get('family', '')} {a.get('given', '')}".strip()
                if name:
                    parts.append(name)
            author_str = ", ".join(parts) + (" 等" if len(authors) > 4 else "")
            year = None
            for k in ("published-print", "published-online", "published", "issued"):
                dp = (it.get(k) or {}).get("date-parts")
                if dp and dp[0]:
                    year = dp[0][0]
                    break
            journal = (it.get("container-title") or [""])[0].strip()
            abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")
            abstract = re.sub(r"\s+", " ", abstract).strip()[:400]
            doi = it.get("DOI", "")
            # 开放获取 PDF 链接（供入库精读）
            oa_pdf = ""
            for link in it.get("link", []) or []:
                if link.get("content-type") == "application/pdf":
                    oa_pdf = link.get("URL", "")
                    break
            out.append({
                "title": title,
                "authors": author_str,
                "year": year,
                "journal": journal,
                "abstract": abstract,
                "url": f"https://doi.org/{doi}" if doi else (it.get("URL") or ""),
                "doi": doi,
                "cited_by": it.get("is-referenced-by-count", 0) or 0,
                "oa_pdf": oa_pdf,
            })
        return out

    def _search_openalex(self, query: str, max_results: int) -> list[dict]:
        """OpenAlex API：检索中文文献（filter=language:zh）。

        免费、无需 Key、返回 JSON。取代原先"搜索引擎 site: 万方"的抓取方案：
        搜索引擎对程序化 site: 查询会返回验证码页或与查询无关的兜底结果
        （实测 0 命中），且失败被静默吞掉，无法察觉。
        """
        import requests

        r = requests.get(
            _OPENALEX_API,
            params={
                "search": query,
                "filter": "language:zh",
                "per-page": max(1, min(int(max_results), 25)),
                "select": "doi,title,display_name,publication_year,language,cited_by_count,"
                          "authorships,primary_location,best_oa_location,"
                          "abstract_inverted_index",
                "mailto": _MAILTO,
            },
            headers={"User-Agent": _UA},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])

        out = []
        for w in results:
            if not isinstance(w, dict):
                continue
            title = (w.get("title") or w.get("display_name") or "").strip()
            if not title:
                continue
            authorships = w.get("authorships") or []
            parts = []
            for a in authorships[:4]:
                if not isinstance(a, dict):
                    continue
                name = ((a.get("author") or {}).get("display_name") or "").strip()
                if name:
                    parts.append(name)
            author_str = ", ".join(parts) + (" 等" if len(authorships) > 4 else "")
            source = ((w.get("primary_location") or {}).get("source") or {})
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            oa_pdf = (w.get("best_oa_location") or {}).get("pdf_url") or ""
            out.append({
                "title": title,
                "authors": author_str,
                "year": w.get("publication_year"),
                "journal": (source.get("display_name") or "").strip(),
                "abstract": _rebuild_abstract(w.get("abstract_inverted_index")),
                "url": w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or "",
                "doi": doi,
                "cited_by": w.get("cited_by_count") or 0,
                "oa_pdf": oa_pdf,
            })
        return out

    @retry(max_attempts=2, backoff=1.5)
    def execute(self, query: str, max_results: int = 5) -> str:
        """执行学术文献检索。"""
        parts = []
        problems = []

        # Crossref：国际期刊（含被引次数 → 重点论文识别；OA 链接 → 供入库）
        # OpenAlex：中文文献（language:zh）
        sources = (
            ("【国际期刊文献 (Crossref)】", self._search_crossref),
            ("【中文文献 (OpenAlex · 中文期刊)】", self._search_openalex),
        )
        for label, search in sources:
            try:
                items = search(query, max_results)
            except Exception as e:
                # 必须把失败显式暴露出来：此前异常被静默吞掉，
                # 中文文献这条路径实际上一次都没成功过，界面上却看不出任何异常
                logger.warning(f"{label} 检索失败: {e}")
                problems.append(f"{label} 失败（{e}）")
                continue
            if not items:
                problems.append(f"{label} 无结果")
                continue
            parts.append(
                label + "\n" + "\n\n".join(_format_item(i, it) for i, it in enumerate(items, 1))
            )

        if not parts:
            detail = "；".join(problems) if problems else "接口无返回"
            return f"学术文献检索失败（{detail}）。请稍后重试或更换关键词。"

        body = "\n\n".join(parts)
        if problems:
            body += "\n\n（部分来源未返回结果：" + "；".join(problems) + "）"
        return f"搜索 '{query}' 的学术文献:\n\n{body}"


# 自动注册
register_tool(AcademicSearchTool())
