# -*- coding: utf-8 -*-
"""搜索工具质量过滤逻辑测试（不依赖网络）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.web_search_cn import (
    _filter_results,
    _is_known_good,
    _junk_score,
    _norm_host,
)


class TestDomainUtils:
    def test_norm_host(self):
        assert _norm_host("https://www.maoyan.com/films") == "maoyan.com"
        assert _norm_host("http://www.baidu.com/link?url=x") == "baidu.com"
        assert _norm_host("https://zh.wikipedia.org/wiki/AI") == "wikipedia.org"
        assert _norm_host("not a url") == ""

    def test_known_good_host(self):
        assert _is_known_good("maoyan.com")
        assert _is_known_good("sub.people.com.cn")
        assert not _is_known_good("tdmai.cn")
        assert not _is_known_good("")


class TestJunkScore:
    def test_short_random_main_domain_is_junk(self):
        assert _junk_score("影院在线", "https://www.tdmai.cn/", "内容", "tdmai.cn") >= 1

    def test_junk_tld_is_junk(self):
        assert _junk_score("最新电影", "https://xx.top/", "内容", "xx.top") >= 1

    def test_aggregator_text_is_junk(self):
        assert _junk_score("影院在线 - 全类型影视片库 免费追剧", "https://www.sogou.com/link?url=x", "随便", "") >= 1

    def test_empty_snippet_is_junk(self):
        assert _junk_score("一个标题", "https://www.example.com/", "", "example.com") >= 1

    def test_normal_result_clean(self):
        assert _junk_score("猫眼电影正在热映", "https://www.maoyan.com/", "当前热映影片列表与票房数据，来自猫眼专业版", "maoyan.com") == 0


class TestFilterResults:
    def test_filter_keeps_good_and_drops_junk(self):
        results = [
            ("猫眼电影正在热映", "https://www.maoyan.com/films", "猫眼电影提供当前上映影片、票房与场次信息，数据来自院线排片。", "maoyan.com"),
            ("影院在线 - 全类型影视片库", "https://www.tdmai.cn/", "影院在线按观影需求细分影视板块，免费追剧。", "tdmai.cn"),
            ("电影票房数据", "https://www.douban.com/movie/", "豆瓣电影提供近期上映影片评分与影评。", "douban.com"),
        ]
        good = _filter_results(results)
        assert len(good) == 2
        assert good[0][0].startswith("猫眼")
        assert good[1][0].startswith("电影票房")
        assert all("影院在线" not in g[0] for g in good)

    def test_filter_handles_href_hint_fallback(self):
        results = [
            ("某垃圾站", "http://www.baidu.com/link?url=abc", "影视大全 免费在线观看高清无删减版", "movie.junk.cn"),
        ]
        assert _filter_results(results) == []

    def test_filter_known_good_via_href(self):
        results = [
            ("人民网新闻", "http://www.baidu.com/link?url=abc", "人民网权威新闻报道。", "www.people.com.cn"),
        ]
        good = _filter_results(results)
        assert len(good) == 1

    def test_mixed_alpha_digit_short_domain_is_junk(self):
        # ypsj88.cn 这类字母数字混合短主域站群
        assert _junk_score("每日快讯直通", "https://www.ypsj88.com/", "内容内容", "ypsj88.com") >= 1

    def test_long_pinyin_domain_is_junk(self):
        # dianyingyuanzuixinshangying 长拼音堆砌域名（SEO 站群）
        host = "dianyingyuanzuixinshangying.cn"
        assert _junk_score("最新上映电影推荐", "https://www.%s/" % host, "内容内容", host) >= 1

    def test_1905_movie_network_clean(self):
        # 正规影视站不被误杀
        assert _junk_score("正在热映电影_1905电影网", "https://www.1905.com/", "犯罪电影发布预告，电影频道资讯", "1905.com") == 0

    def test_chinese_host_not_junk_by_length(self):
        # 百度 c-showurl 提取到"百家号"这类中文站点名，不能按短域名误杀
        assert _junk_score("给阿嬷的情书", "http://www.baidu.com/link?url=x", "一封情书藏着百年乡愁，剧情与情感并重。", "百家号") == 0

    def test_platform_mark_trusted_even_short_snippet(self):
        # 360 跳转链接摘要短，但标题带知名平台 → 放行
        results = [
            ("《给阿嫲的情书》也是给大家的一封情书_哔哩哔哩_bilibili", "https://www.so.com/link?m=x", ""),
        ]
        assert _filter_results(results) == results
