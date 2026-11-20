"""相似论文推荐：复用 AcademicSearchTool 的 Crossref / OpenAlex 检索。

相似度 = 0.55*词重叠 + 0.20*时效 + 0.15*被引归一 + 0.10*开放获取。
结果缓存 7 天；网络失败返回空列表 + error，不抛 500。
"""
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

from tools.academic_search import AcademicSearchTool
from utils.logger import get_logger

logger = get_logger(__name__)

CACHE_DAYS = 7
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]+")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    toks = {w for w in _LATIN_RE.findall(text) if len(w) >= 2}
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            toks.add(run)
        else:
            toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _overlap(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return min(1.0, len(query_tokens & candidate_tokens) / len(query_tokens))


def _recency(year) -> float:
    try:
        y = int(year)
    except (TypeError, ValueError):
        return 0.2
    age = max(0, datetime.date.today().year - y)
    if age <= 1:
        return 1.0
    if age <= 3:
        return 0.85
    if age <= 5:
        return 0.7
    if age <= 10:
        return 0.5
    return 0.25


def _cited_norm(cited) -> float:
    try:
        return min(1.0, max(0.0, float(cited) / 100.0))
    except (TypeError, ValueError):
        return 0.0


def _dedup_key(item: dict) -> str:
    doi = (item.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    title = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", (item.get("title") or "").lower())
    return "title:" + title


def _shared_points(item: dict, keywords: list[str]) -> str:
    haystack = ((item.get("title") or "") + " " + (item.get("abstract") or "")).lower()
    shared = [k for k in keywords if k and k.lower() in haystack]
    if shared:
        return (
            "共同关键词：" + "、".join(shared[:4]) +
            "。两篇论文主题相近，可对照其方法与实验设计。"
        )
    return "研究主题与你的论文相近，建议对照其研究框架与结论。"


def _query_terms(parsed: dict) -> list[str]:
    title = (parsed.get("title") or "").strip()
    keywords = [k for k in (parsed.get("keywords") or []) if k]
    terms = []
    if title and len(title) >= 4:
        terms.append(title)
    if keywords:
        terms.append(" ".join(keywords[:4]))
    if not terms:
        abstract = (parsed.get("abstract") or "").strip()
        if abstract:
            terms.append(abstract[:60])
    return terms[:2]


def _cache_fresh(data: dict | None) -> bool:
    if not data or not data.get("items"):
        return False
    try:
        ts = datetime.datetime.strptime(data.get("updated_at", ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return (datetime.datetime.now() - ts) <= datetime.timedelta(days=CACHE_DAYS)


class SimilarCancelled(Exception):
    """用户取消相似论文检索。"""


def find_similar(parsed: dict, requirement: dict | None = None, limit: int = 10,
                 refresh: bool = False, paper_id: str = "", progress=None,
                 should_cancel=None) -> dict:
    """检索相似论文；paper_id 存在时读写 similar.json 缓存。

    progress(percent, stage) 汇报进度；should_cancel() 为真时抛 SimilarCancelled。
    """
    from paper import store  # 延迟导入，避免循环依赖

    def report(percent: int, stage: str) -> None:
        if progress:
            progress(max(0, min(100, int(percent))), stage)

    def check() -> None:
        if should_cancel and should_cancel():
            raise SimilarCancelled("相似论文检索已取消")

    check()
    if paper_id and not refresh:
        cached = store.load_similar(paper_id)
        if _cache_fresh(cached):
            report(100, "已使用 7 天内的缓存结果")
            return {**cached, "cached": True}

    terms = _query_terms(parsed)
    if not terms:
        return {"items": [], "error": "论文缺少标题与关键词，无法检索相似论文",
                "updated_at": _now(), "cached": False}

    keywords = [str(k) for k in (parsed.get("keywords") or [])]
    tool = AcademicSearchTool()
    per_query = max(8, min(int(limit or 10) * 2, 20))
    collected: dict[str, dict] = {}
    errors: list[str] = []
    total_queries = max(1, len(terms) * 2)
    done = 0

    jobs = [(term, name, search) for term in terms for name, search in (
        ("Crossref", tool._search_crossref), ("OpenAlex", tool._search_openalex),
    )]
    batches = {}
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        futures = {}
        for index, (term, name, search) in enumerate(jobs):
            check()
            futures[pool.submit(search, term, per_query)] = (index, name)
        for future in as_completed(futures):
            try:
                check()
            except SimilarCancelled:
                for pending in futures:
                    pending.cancel()
                raise
            index, source_name = futures[future]
            try:
                batches[index] = future.result() or []
            except Exception as e:
                logger.warning(f"相似论文检索失败 {source_name}: {e}")
                errors.append(f"{source_name} 检索失败")
            done += 1
            report(5 + int(65 * done / total_queries),
                   f"已完成 {done}/{total_queries} 路检索：{source_name}")
    # Merge in query order so equal-score results are stable across runs.
    for index in sorted(batches):
        for it in batches[index]:
            if not isinstance(it, dict) or not (it.get("title") or "").strip():
                continue
            key = _dedup_key(it)
            if key not in collected:
                collected[key] = dict(it)

    check()
    if not collected:
        detail = "；".join(dict.fromkeys(errors)) or "未检索到相关论文"
        return {"items": [], "error": detail, "updated_at": _now(), "cached": False}

    report(75, "正在按相关度排序…")
    query_tokens = _tokens(" ".join(terms) + " " + " ".join(keywords))
    scored = []
    for it in collected.values():
        cand_tokens = _tokens(
            (it.get("title") or "") + " " + (it.get("abstract") or "") + " " +
            (it.get("journal") or "")
        )
        score = (
            0.55 * _overlap(query_tokens, cand_tokens)
            + 0.20 * _recency(it.get("year"))
            + 0.15 * _cited_norm(it.get("cited_by"))
            + 0.10 * (1.0 if it.get("oa_pdf") else 0.0)
        )
        item = {
            "title": it.get("title") or "",
            "authors": it.get("authors") or "",
            "year": it.get("year"),
            "journal": it.get("journal") or "",
            "abstract": it.get("abstract") or "",
            "doi": it.get("doi") or "",
            "url": it.get("url") or "",
            "cited_by": it.get("cited_by") or 0,
            "oa": bool(it.get("oa_pdf")),
            "oa_pdf": it.get("oa_pdf") or "",
            "similarity": round(score, 4),
            "similar_points": _shared_points(it, keywords),
        }
        scored.append(item)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    items = scored[:max(1, min(int(limit or 10), 20))]
    result = {
        "items": items,
        "queries": terms,
        "errors": list(dict.fromkeys(errors)),
        "error": "" if not errors or items else "；".join(dict.fromkeys(errors)),
        "updated_at": _now(),
        "cached": False,
        "cache_days": CACHE_DAYS,
    }
    check()
    if paper_id:
        try:
            store.save_similar(paper_id, result)
        except Exception as e:
            logger.warning(f"相似论文缓存写入失败: {e}")
    report(100, "检索完成")
    return result
