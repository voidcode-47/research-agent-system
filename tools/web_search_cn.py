"""国内可用搜索工具：多引擎自动切换 + 垃圾站过滤，无需 API Key。

引擎顺序：百度 → 搜狗 → 360 → 必应 RSS。
- 每个引擎解析结果后先做质量过滤（SEO 垃圾站 / AI 聚合站 / 无实质摘要），
  有效结果不足 2 条时自动切换下一个引擎，直到找到可用结果。
- 百度/搜狗/360 的链接多为跳转链接：百度会从结果容器里提取真实来源域名
  （c-showurl / cosc-source-text）用于垃圾域名判定；拿不到真实域名时，
  按标题/摘要聚合站特征 + 摘要实质内容过滤。
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

# 垃圾站特征（标题/摘要中出现，多半是 AI 聚合站或 SEO 站群）
_JUNK_TEXT_MARKS = (
    "在线观看", "免费看", "免费追剧", "影视片库", "追剧", "全集", "电影天堂",
    "聚合平台", "资源导航", "网址导航", "影视大全", "无删减", "高清在线",
    "影院在线", "全类型影视", "随心追", "精选笔记", "观影指南", "大片推荐",
    "盘点", "影评合集", "片单",
)
# 站群常用 TLD（AI 垃圾站高发）
_JUNK_TLDS = (
    ".top", ".xyz", ".icu", ".vip", ".cc", ".club", ".online",
    ".site", ".fun", ".live", ".click", ".link", ".buzz", ".store", ".shop",
)
# 知名内容平台特征词（标题/摘要命中即视为可信来源，避免摘要短被误杀）
_PLATFORM_MARKS = (
    "bilibili", "哔哩哔哩", "爱奇艺", "iqiyi", "优酷", "youku", "腾讯视频",
    "好看视频", "1905", "豆瓣", "douban", "猫眼", "maoyan", "网易", "百家号",
    "微信公众号", "微信公众平台", "央视", "人民网", "新华网", "知乎", "微博",
    "快手", "抖音", "今日头条", "澎湃", "36氪",
)
# 已知可信来源（命中即视为有效结果）
_KNOWN_GOOD_HOSTS = (
    "baidu.com", "zhihu.com", "weibo.com", "douban.com", "163.com", "qq.com",
    "sina.com.cn", "sohu.com", "people.com.cn", "xinhuanet.com", "cctv.com",
    "gov.cn", "edu.cn", "mtime.com", "maoyan.com", "taopiaopiao.com", "dianping.com",
    "imdb.com", "bilibili.com", "youku.com", "iqiyi.com", "tencent.com",
    "aliyun.com", "github.com", "csdn.net", "jianshu.com", "36kr.com",
    "thepaper.cn", "ifeng.com", "huanqiu.com", "caixin.com", "yicai.com",
    "wikipedia.org", "nature.com", "science.org", "arxiv.org", "springer.com",
    "elsevier.com", "ieee.org", "acm.org", "semanticscholar.org",
    "v2ex.com", "1905.com", "mtime.com",
)


def _norm_host(url: str) -> str:
    """从 URL 提取小写主域名；跳转链接/无域名时返回空串（无法判定）。"""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        host = host.split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        # 剥离常见语言子域（zh.wikipedia.org → wikipedia.org），避免短主域误判
        _lang = ("zh", "en", "de", "fr", "ja", "ko", "ru", "es", "it", "pt", "ar", "hi",
                 "id", "tr", "nl", "pl", "sv", "fi", "da", "no", "cs", "hu", "ro", "uk")
        parts = host.split(".")
        if len(parts) >= 3 and parts[0] in _lang:
            host = ".".join(parts[1:])
        return host
    except Exception:
        return ""


def _is_known_good(host: str) -> bool:
    if not host:
        return False
    return any(host == g or host.endswith("." + g) for g in _KNOWN_GOOD_HOSTS)


def _junk_score(title: str, href: str, snippet: str, host: str) -> int:
    """返回疑似垃圾站得分，≥1 视为垃圾结果（宁可漏判不多返回垃圾）。"""
    s = 0
    text = (title or "") + " " + (snippet or "")
    # 1) 域名可判定时（仅 ASCII 域名；中文站点名如"百家号"不做长度判定）：垃圾 TLD / 短随机主域 / 长拼音堆砌主域
    if host and host.isascii():
        if any(host.endswith(t) for t in _JUNK_TLDS):
            s += 1
        else:
            main = host.split(".")[0]
            # 主域 ≤5 字符且非纯数字（tdmai.cn 这类站群域名）
            if len(main) <= 5 and not main.isdigit():
                s += 1
            # 主域 >18 字符且全字母：关键词拼音堆砌域名，多为 SEO 站群
            elif len(main) > 18 and main.isalpha():
                s += 1
            # 主域 ≤6 且字母数字混合（ypsj88.cn 这类站群，非纯数字 IP 类）
            elif len(main) <= 6 and not main.isdigit() and any(c.isdigit() for c in main):
                s += 1
    # 2) 标题/摘要聚合站特征
    if any(m in text for m in _JUNK_TEXT_MARKS):
        s += 1
    # 3) 摘要无实质内容（去掉标题后太短或为空）
    snippet_clean = (snippet or "").replace(title or "", "").strip()
    if len(snippet_clean) < 15:
        s += 1
    return s


# 搜索引擎自身的跳转域名：链接指向它们时，真实来源域名必须依赖 host_hint
_SEARCH_HOSTS = ("baidu.com", "sogou.com", "so.com", "bing.com")


def _filter_results(results: list[tuple]) -> list[tuple]:
    """按质量过滤；每条结果为 (title, href, snippet[, host_hint])。

    顺序：可信域名 → 垃圾特征 → 知名平台 → 综合评分。
    """
    good = []
    for item in results:
        title, href, snippet = item[0], item[1], item[2]
        host_hint = item[3] if len(item) > 3 else ""
        href_host = _norm_host(href)
        # 百度/搜狗/360 的链接是跳转链接：真实域名只来自 hint；否则用 href 域名
        if href_host in _SEARCH_HOSTS:
            host = host_hint
        else:
            host = href_host or host_hint
        if _is_known_good(host):
            good.append((title, href, snippet))
            continue
        text = (title or "") + " " + (snippet or "")
        # 垃圾特征词优先过滤（盗版/聚合站即使出现在平台标题也滤掉）
        if any(m in text for m in _JUNK_TEXT_MARKS):
            continue
        # 知名平台特征 → 信任（解决 360/搜狗跳转链接摘要短被误杀）
        if any(m in text for m in _PLATFORM_MARKS):
            good.append((title, href, snippet))
            continue
        if _junk_score(title, href, snippet, host) == 0:
            good.append((title, href, snippet))
    return good


class WebSearchCNTool(BaseTool):
    """国内网络可用搜索：百度→搜狗→360→必应 自动切换，垃圾站自动过滤。"""

    name = "web_search_cn"
    description = (
        "搜索互联网获取最新信息（国内网络可用）。"
        "自动在 百度/搜狗/360/必应 之间切换并过滤垃圾网站，"
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

    # ---------- 引擎：百度（返回真实来源域名提示用于过滤） ----------
    def _search_baidu(self, query: str, max_results: int) -> list[tuple]:
        """百度搜索结果页解析；从容器提取真实来源域名（c-showurl）。"""
        import requests
        from lxml import html

        text = self._fetch("https://www.baidu.com/s", {"wd": query})
        tree = html.fromstring(text)
        results: list[tuple] = []

        for h3 in tree.xpath("//h3/a"):
            title = " ".join(h3.text_content().split())
            if not title:
                continue
            href = h3.get("href", "")
            if "广告" in title or "baidu.com/c/guest" in href:
                continue
            # 摘要与真实来源域名：取最近的结果容器
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
            real_host = ""
            if container is not None:
                snippet = " ".join(container.text_content().split())
                snippet = snippet.replace(title, "", 1).strip()[:220]
                # 提取真实来源：优先 c-showurl 容器内 <a> 的真实链接，
                # 拿不到时回退显示文本（可能是中文站点名，如"百家号"）
                show = container.xpath(
                    ".//*[contains(@class,'c-showurl') or contains(@class,'cosc-source-text')]"
                )
                if show:
                    a = show[0].xpath(".//a/@href")
                    if a:
                        real_host = a[0]
                    else:
                        real_host = "".join(show[0].itertext()).strip().split()[0] if show[0].itertext() else ""
            results.append((title, href, snippet, real_host))
            if len(results) >= max_results:
                break
        return results

    # ---------- 引擎：搜狗 ----------
    def _search_sogou(self, query: str, max_results: int) -> list[tuple]:
        """搜狗搜索结果解析（跳转链接，靠标题/摘要特征过滤）。"""
        import requests
        from lxml import html

        text = self._fetch("https://www.sogou.com/web", {"query": query})
        tree = html.fromstring(text)
        results: list[tuple] = []

        for h3 in tree.xpath("//h3/a"):
            title = " ".join(h3.text_content().split())
            if not title:
                continue
            href = h3.get("href", "")
            if not href or href.startswith("javascript"):
                continue
            node = h3
            container = None
            for _ in range(5):
                node = node.getparent()
                if node is None:
                    break
                if "vrwrap" in node.get("class", ""):
                    container = node
                    break
            snippet = ""
            if container is not None:
                snippet = " ".join(container.text_content().split())
                snippet = snippet.replace(title, "", 1).strip()[:220]
            results.append((title, href, snippet))
            if len(results) >= max_results:
                break
        return results

    # ---------- 引擎：360 ----------
    def _search_360(self, query: str, max_results: int) -> list[tuple]:
        """360 搜索结果解析（跳转链接，靠标题/摘要特征过滤）。"""
        import requests
        from lxml import html

        text = self._fetch("https://www.so.com/s", {"q": query})
        tree = html.fromstring(text)
        results: list[tuple] = []

        for h3 in tree.xpath("//h3/a"):
            title = " ".join(h3.text_content().split())
            if not title:
                continue
            href = h3.get("href", "")
            if not href or href.startswith("javascript"):
                continue
            node = h3
            container = None
            for _ in range(5):
                node = node.getparent()
                if node is None:
                    break
                if "res-list" in node.get("class", ""):
                    container = node
                    break
            snippet = ""
            if container is not None:
                snippet = " ".join(container.text_content().split())
                snippet = snippet.replace(title, "", 1).strip()[:220]
            results.append((title, href, snippet))
            if len(results) >= max_results:
                break
        return results

    # ---------- 引擎：必应 RSS（中文长查询加引号防分词拆散） ----------
    def _search_bing_rss(self, query: str, max_results: int) -> list[tuple]:
        """必应 RSS 接口解析；中文短语用引号包住，避免把整句拆成单字。"""
        import requests
        from lxml import etree

        bing_q = query
        # 中文长查询：加引号作为短语查询，减少"给阿嫲的一封情书"被拆成"给"字的情况
        if any("\u4e00" <= ch <= "\u9fff" for ch in query) and len(query) > 4:
            bing_q = f'"{query}"'
        text = self._fetch(
            "https://www.bing.com/search",
            {"q": bing_q, "format": "rss", "count": max_results},
        )
        root = etree.fromstring(text.encode("utf-8"))
        results: list[tuple] = []
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
        """执行搜索：引擎链 百度→搜狗→360→必应，有效结果不足则自动切换。"""
        try:
            import requests  # noqa: F401
        except ImportError:
            return "缺少依赖 requests，请运行 pip install requests"

        engines = [
            ("百度", self._search_baidu),
            ("搜狗", self._search_sogou),
            ("360", self._search_360),
            ("必应", self._search_bing_rss),
        ]
        notes = []
        for source, fn in engines:
            try:
                raw = fn(query, max_results)
                good = _filter_results(raw)
                if len(good) >= 2:
                    return self._format(query, good, source)
                notes.append(f"{source} 有效结果不足（{len(good)}/{len(raw)} 条被过滤）")
            except Exception as e:
                notes.append(f"{source} 失败: {e}")
                logger.debug(f"{source} 搜索失败: {e}")

        return (
            f"搜索未能获得有效结果（{'；'.join(notes)}）。"
            "建议更换关键词、缩小范围，或直接访问相关官方网站/平台。"
        )

    def _format(self, query: str, results: list[tuple], source: str) -> str:
        formatted = []
        for i, (title, href, snippet) in enumerate(results, 1):
            formatted.append(f"{i}. {title}\n   {snippet}\n   来源: {href}")
        return f"搜索 '{query}' 的结果（来源: {source}）:\n\n" + "\n\n".join(formatted)


# 自动注册
register_tool(WebSearchCNTool())
