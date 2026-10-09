"""网页抓取工具，用 trafilatura 提取正文。"""
from typing import Optional

from tools.base import BaseTool, register_tool
from utils.logger import get_logger
from utils.retry import retry

logger = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# HTML 响应体上限：超过则截断，避免病态页面耗尽内存
_MAX_HTML_BYTES = 5 * 1024 * 1024


class WebScraperTool(BaseTool):
    """网页正文提取，剔除导航/广告。"""

    name = "web_scraper"
    description = "抓取指定网页的正文内容。输入网址 URL，返回页面标题和正文文本。"
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的网页 URL",
            },
            "max_length": {
                "type": "integer",
                "description": "最大返回字符数，默认 2000（控制 token 消耗）",
            },
        },
        "required": ["url"],
    }

    def _fetch(self, url: str) -> Optional[str]:
        """抓取网页 HTML。

        优先 requests + 浏览器 UA（兼容更多网站，如部分有反爬/UA 校验的站点），
        失败回退 trafilatura.fetch_url。响应体流式读取并截断，避免超大页面吃满内存。
        """
        try:
            import requests
            resp = requests.get(
                url,
                headers={
                    "User-Agent": _UA,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=20,
                stream=True,
            )
            try:
                if resp.status_code == 200:
                    chunks = []
                    size = 0
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= _MAX_HTML_BYTES:
                            logger.debug(
                                f"网页超过 {_MAX_HTML_BYTES // (1024 * 1024)}MB 上限，已截断: {url}"
                            )
                            break
                    if chunks:
                        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
                        return b"".join(chunks).decode(encoding, errors="replace")
                logger.debug(f"requests 抓取状态码 {resp.status_code}: {url}")
            finally:
                resp.close()
        except Exception as e:
            logger.debug(f"requests 抓取失败 {url}: {e}")

        try:
            from trafilatura import fetch_url
            return fetch_url(url)
        except Exception as e:
            logger.debug(f"trafilatura 抓取失败 {url}: {e}")
        return None

    @retry(max_attempts=2, backoff=1.5)
    def execute(self, url: str, max_length: int = 2000) -> str:
        """抓取网页正文。"""
        try:
            import trafilatura
            from trafilatura import extract
        except ImportError:
            return "trafilatura 未安装，请运行 pip install trafilatura"

        html_text = self._fetch(url)
        if not html_text:
            return (
                f"无法获取网页内容: {url}\n"
                "可能原因：网站对当前网络不可达（连接超时）、有反爬限制，或需要浏览器登录。"
                "可尝试搜索该页面的其他来源（如新闻转载、快照）。"
            )

        text = extract(
            html_text,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )

        if not text:
            return f"网页 {url} 未提取到正文内容"

        # 获取标题
        metadata = trafilatura.extract_metadata(html_text)
        title = metadata.title if metadata and metadata.title else "未知标题"

        # 截断
        if len(text) > max_length:
            text = text[:max_length] + "...(内容已截断)"

        return f"标题: {title}\n来源: {url}\n\n{text}"


register_tool(WebScraperTool())
