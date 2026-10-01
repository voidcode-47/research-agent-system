"""学术文献检索工具。

来源策略（均免费、无需 Key、国内网络可用）：
1. Crossref API —— 国际期刊文献（含部分中文学术期刊），返回标题/作者/年份/
   期刊/摘要/DOI 链接，接口稳定开放。
2. 必应学术站点检索 —— 限定 site:cnki.net（知网）/ wanfangdata.com.cn（万方），
   返回中文论文题录与摘要片段。

说明：学术全文受版权与平台权限限制，本工具提供题录+摘要，足以支撑研究性
问题的文献调研；如需全文可在结果链接处访问（知网/万方部分需机构权限）。
"""
import re

from tools.base import BaseTool, register_tool
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class AcademicSearchTool(BaseTool):
    """学术文献检索：Crossref 国际期刊 + 必应索引的中文知网/万方论文。"""

    name = "academic_search"
    description = (
        "学术文献检索（论文），返回论文标题、作者、年份、期刊/来源、摘要与链接。"
        "中文文献来自万方数据，国际文献来自 Crossref（含中文学术期刊）。"
        "适合研究性问题的文献调研。优先选择与主题相关的期刊论文，"
        "书籍、教程类文献优先级低。"
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
                # Crossref 礼貌池要求：提供联系邮箱以获得更高配额（可替换为真实邮箱）
                "mailto": "research-assistant@users.noreply.github.com",
            },
            headers={"User-Agent": "research-assistant/1.0 (mailto:research-assistant@users.noreply.github.com)"},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json()["message"]["items"]

        out = []
        for it in items:
            title = (it.get("title") or [""])[0].strip()
            if not title:
                continue
            authors = it.get("author", [])
            parts = []
            for a in authors[:4]:
                name = f"{a.get('family', '')} {a.get('given', '')}".strip()
                if name:
                    parts.append(name)
            author_str = ", ".join(parts) + (" 等" if len(authors) > 4 else "")
            year = None
            for k in ("published-print", "published-online", "published", "issued"):
                dp = it.get(k, {}).get("date-parts")
                if dp and dp[0]:
                    year = dp[0][0]
                    break
            journal = (it.get("container-title") or [""])[0].strip()
            abstract = re.sub(r"<[^>]+>", " ", it.get("abstract", ""))
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

    def _search_cn_sites(self, query: str, max_results: int) -> list[dict]:
        """百度搜索限定万方数据站点，返回中文论文题录与摘要。

        注：site:cnki.net 在百度会触发安全验证页（知网被特殊处理），
        故中文文献以万方（wanfangdata.com.cn）为主，知网收录的
        中文学术期刊大多也可在 Crossref 检索到。
        """
        import requests
        from lxml import html

        r = requests.get(
            "https://www.baidu.com/s",
            params={"wd": f"site:wanfangdata.com.cn {query}"},
            headers=_HEADERS,
            timeout=12,
        )
        r.raise_for_status()
        tree = html.fromstring(r.text)
        results = []
        for h3 in tree.xpath("//h3/a"):
            title = " ".join(h3.text_content().split())
            if not title or "广告" in title:
                continue
            href = h3.get("href", "")
            # 摘要：取最近的结果容器文本
            node, container = h3, None
            for _ in range(6):
                node = node.getparent()
                if node is None:
                    break
                cls = node.get("class", "")
                if any(k in cls for k in ("cosc-card-content", "c-container", "result")):
                    container = node
                    break
            snippet = ""
            if container is not None:
                snippet = " ".join(container.text_content().split())
                snippet = snippet.replace(title, "", 1).strip()[:300]
            results.append({"title": title, "abstract": snippet, "url": href})
            if len(results) >= max_results:
                break
        return results

    @retry(max_attempts=2, backoff=1.5)
    def execute(self, query: str, max_results: int = 5) -> str:
        """执行学术文献检索。"""
        parts = []

        # 1) Crossref 国际期刊（含被引次数 → 重点论文识别；OA 链接 → 供入库）
        try:
            items = self._search_crossref(query, max_results)
            if items:
                lines = []
                for i, it in enumerate(items, 1):
                    meta = " | ".join(x for x in [
                        it["authors"], str(it["year"]) if it["year"] else "", it["journal"]
                    ] if x)
                    cited = f"被引 {it['cited_by']} 次" if it["cited_by"] else "新近论文"
                    oa_mark = " 📄开放全文" if it["oa_pdf"] else ""
                    lines.append(
                        f"[{i}] {it['title']}{oa_mark}\n"
                        f"    {meta} | {cited}\n"
                        f"    {it['abstract'] or '（该库未提供摘要）'}\n"
                        f"    {it['url']}"
                    )
                parts.append("【国际期刊文献 (Crossref)】\n" + "\n\n".join(lines))
        except Exception as e:
            logger.warning(f"Crossref 检索失败: {e}")

        # 2) 中文知网/万方（经必应索引）
        try:
            items = self._search_cn_sites(query, max_results)
            if items:
                lines = []
                for i, it in enumerate(items, 1):
                    lines.append(
                        f"{i}. {it['title']}\n"
                        f"   {it['abstract'] or '（无摘要）'}\n"
                        f"   {it['url']}"
                    )
                parts.append("【中文文献 (知网/万方)】\n" + "\n\n".join(lines))
        except Exception as e:
            logger.warning(f"中文文献检索失败: {e}")

        if not parts:
            return f"学术文献检索失败（网络或接口异常），请稍后重试或更换关键词。"
        return f"搜索 '{query}' 的学术文献:\n\n" + "\n\n".join(parts)


# 自动注册
register_tool(AcademicSearchTool())
