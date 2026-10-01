"""国内可用搜索工具：百度优先，必应 RSS 兜底，无需 API Key。

DuckDuckGo（web_search）在国内网络环境经常不可用；必应对中文长查询
分词差、相关性低。本工具在百度搜索结果页解析结果（中文相关性最好），
失败时回退必应 RSS 接口，保证国内网络下可用。
"""
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


class WebSearchCNTool(BaseTool):
    """国内网络可用搜索：百度优先，必应 RSS 兜底。"""

    name = "web_search_cn"
    description = (
        "搜索互联网获取最新信息（国内网络可用，自动选用百度/必应）。"
        "输入搜索关键词，返回相关网页标题、摘要和链接。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数，默认 5",
            },
        },
        "required": ["query"],
    }

    def _fetch(self, url: str, params: dict, timeout: int = 12) -> str:
        import requests
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text

    def _search_baidu(self, query: str, max_results: int) -> list[tuple[str, str, str]]:
        """百度搜索结果页解析。"""
        import requests
        from lxml import html

        text = self._fetch("https://www.baidu.com/s", {"wd": query})
        tree = html.fromstring(text)
        results: list[tuple[str, str, str]] = []

        for h3 in tree.xpath("//h3/a"):
            title = " ".join(h3.text_content().split())
            if not title:
                continue
            href = h3.get("href", "")
            # 跳过明显广告（百度广告容器/标题带广告标记）
            if "广告" in title or "baidu.com/c/guest" in href:
                continue
            # 摘要：取最近的结果容器文本（百度新版 cosc-card-content / 旧版 c-container）
            node = h3
            container = None
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
                snippet = snippet.replace(title, "", 1).strip()
                snippet = snippet[:200]
            results.append((title, href, snippet))
            if len(results) >= max_results:
                break
        return results

    def _search_bing_rss(self, query: str, max_results: int) -> list[tuple[str, str, str]]:
        """必应 RSS 接口解析（HTML 版必应对中文长查询分词差，RSS 稍好且稳定）。"""
        import requests
        from lxml import etree

        text = self._fetch(
            "https://www.bing.com/search",
            {"q": query, "format": "rss", "count": max_results},
        )
        root = etree.fromstring(text.encode("utf-8"))
        ns = {"dc": "http://purl.org/dc/elements/1.1/"}
        results: list[tuple[str, str, str]] = []
        for item in root.xpath("//item"):
            title = " ".join(item.xpath("string(title)").split())
            link = item.xpath("string(link)").strip()
            desc = " ".join(item.xpath("string(description)").split())
            if title and link:
                results.append((title, link, desc))
            if len(results) >= max_results:
                break
        return results

    @retry(max_attempts=2, backoff=1.5)
    def execute(self, query: str, max_results: int = 5) -> str:
        """执行搜索：百度优先，必应 RSS 兜底。"""
        try:
            import requests  # noqa: F401
        except ImportError:
            return "缺少依赖 requests，请运行 pip install requests"

        errors = []
        # 1) 百度（中文相关性最好）
        try:
            results = self._search_baidu(query, max_results)
            if results:
                return self._format(query, results, "百度")
            errors.append("百度无结果")
        except Exception as e:
            errors.append(f"百度失败: {e}")
            logger.debug(f"百度搜索失败: {e}")

        # 2) 必应 RSS 兜底
        try:
            results = self._search_bing_rss(query, max_results)
            if results:
                return self._format(query, results, "必应")
            errors.append("必应无结果")
        except Exception as e:
            errors.append(f"必应失败: {e}")
            logger.debug(f"必应 RSS 失败: {e}")

        return f"搜索失败（{'; '.join(errors)}）。请稍后重试或更换关键词。"

    def _format(self, query: str, results: list[tuple[str, str, str]], source: str) -> str:
        formatted = []
        for i, (title, href, snippet) in enumerate(results, 1):
            formatted.append(f"{i}. {title}\n   {snippet}\n   来源: {href}")
        return f"搜索 '{query}' 的结果（来源: {source}）:\n\n" + "\n\n".join(formatted)


# 自动注册
register_tool(WebSearchCNTool())
