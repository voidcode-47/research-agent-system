"""研究助手 Web 服务器（FastAPI + 静态 SPA 前端）。

替代 Streamlit 前端：提供 JSON API + SSE 流式接口，
前端为纯 HTML/JS 单页应用（web/ 目录），页面切换零重载。

启动方式:
    python server.py            # 等价于 uvicorn server:app --host 127.0.0.1 --port 8501
"""
import datetime
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import settings
from config.llm_config import PROVIDER_CONFIG, list_providers
from config.prompts import DIRECT_CHAT_SYSTEM_PROMPT
from llm.factory import LLMFactory, normalize_base_url
from utils.logger import get_logger
from utils.prefs import load_prefs, save_prefs
from utils.env_file import update_env

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "data", "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

app = FastAPI(title="研究助手 - 多智能体系统", version="2.0.0")

# ---------------------------------------------------------------------------
# 失败报告判定（与 research_page 同逻辑，避免依赖 Streamlit 模块）
# ---------------------------------------------------------------------------
_FAIL_MARKERS = ("⚠️ API 限流（请求过于频繁）", "API 调用失败", "报告生成失败")


def _is_report_ok(text: str) -> bool:
    """判断报告内容是否为有效生成结果（非限流/调用失败/降级产物）。"""
    if not text:
        return False
    return not any(marker in text for marker in _FAIL_MARKERS)


def _safe_report_path(name: str) -> str:
    """校验报告文件名并返回绝对路径（防目录穿越）。"""
    base = os.path.basename(name)
    if base != name or not base.endswith(".md"):
        raise HTTPException(400, "非法文件名")
    fpath = os.path.join(REPORT_DIR, base)
    if not os.path.isfile(fpath):
        raise HTTPException(404, "报告不存在")
    return fpath


# ---------------------------------------------------------------------------
# 向量库单例（无 UI 依赖，带失败冷却）
# ---------------------------------------------------------------------------
_vs_singleton = None
_vs_last_fail_ts = 0.0
_VS_FAIL_COOLDOWN = 10.0


def get_vector_store():
    """获取 VectorStore 单例；embedding 不可用返回 None。"""
    global _vs_singleton, _vs_last_fail_ts
    if _vs_singleton is not None:
        return _vs_singleton
    if time.monotonic() - _vs_last_fail_ts < _VS_FAIL_COOLDOWN:
        return None
    try:
        from tools.vector_store import VectorStore
        from llm.embedding import EmbeddingProvider
        ep = EmbeddingProvider()
        _vs_singleton = VectorStore(
            persist_dir=settings.CHROMA_PERSIST_DIR,
            embedding_fn=ep,
        )
        return _vs_singleton
    except Exception as e:
        logger.warning(f"VectorStore 初始化失败: {e}")
        _vs_last_fail_ts = time.monotonic()
        return None


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    messages: list[dict] = []
    mode: str = "auto"          # auto | deep | chat
    tools: list[str] = []
    use_rag: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None


class ResearchRequest(BaseModel):
    topic: str
    max_iterations: int = 8
    max_results: int = 5
    use_rag: bool = False
    detailed: bool = True
    pre_search: bool = True
    auto_ingest: bool = True
    provider: Optional[str] = None
    model: Optional[str] = None


class SettingsRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    custom_base_url: Optional[str] = None
    custom_api_key: Optional[str] = None
    custom_model: Optional[str] = None


class HealthCheckRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None


class KbCreateRequest(BaseModel):
    name: str


class KbUrlRequest(BaseModel):
    url: str
    collection: str = "default"


class KbSearchRequest(BaseModel):
    collection: str = "default"
    query: str
    k: int = 5


# ---------------------------------------------------------------------------
# SSE 工具
# ---------------------------------------------------------------------------
def _sse(payload: dict) -> str:
    import json
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 设置 / 模型
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return {"ok": True, "version": "2.0.0"}


@app.get("/api/providers")
def api_providers():
    prefs = load_prefs()
    return {
        "providers": list_providers(),
        "labels": {k: v["label"] for k, v in PROVIDER_CONFIG.items()},
        "current_provider": prefs.get("provider"),
        "current_model": prefs.get("model"),
        "custom_models": prefs.get("custom_models", {}),
        "custom_api": {
            "base_url": settings.CUSTOM_API_BASE_URL,
            "api_key": bool(settings.CUSTOM_API_KEY),
            "model": settings.CUSTOM_API_MODEL,
        },
    }


@app.get("/api/models")
def api_models(provider: str):
    try:
        models = LLMFactory.get_models(provider)
        return {"models": models, "provider": provider}
    except Exception as e:
        return {"models": [], "provider": provider, "error": str(e)}


@app.post("/api/health-check")
def api_health_check(req: HealthCheckRequest):
    provider = req.provider or load_prefs().get("provider") or settings.DEFAULT_LLM_PROVIDER
    model = req.model
    return LLMFactory.health_check(provider, model)


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest):
    prefs = load_prefs()
    provider = req.provider or prefs.get("provider") or settings.DEFAULT_LLM_PROVIDER
    model = req.model if req.model is not None else prefs.get("model", "")
    custom_models = dict(prefs.get("custom_models", {}))
    if req.custom_model is not None:
        custom_models[provider] = req.custom_model
    save_prefs(provider, model, custom_models)

    # 自定义 API 配置写入 .env 并热更新运行时配置
    if req.custom_base_url is not None or req.custom_api_key is not None or req.custom_model is not None:
        updates = {}
        if req.custom_base_url is not None:
            updates["CUSTOM_API_BASE_URL"] = req.custom_base_url.strip()
            settings.CUSTOM_API_BASE_URL = updates["CUSTOM_API_BASE_URL"]
        if req.custom_api_key is not None:
            updates["CUSTOM_API_KEY"] = req.custom_api_key.strip()
            settings.CUSTOM_API_KEY = updates["CUSTOM_API_KEY"]
        if req.custom_model is not None:
            updates["CUSTOM_API_MODEL"] = req.custom_model.strip()
            settings.CUSTOM_API_MODEL = updates["CUSTOM_API_MODEL"]
        if updates:
            update_env(updates)
    return {"ok": True, "provider": provider, "model": model}


# ---------------------------------------------------------------------------
# 聊天（SSE 流式）
# ---------------------------------------------------------------------------
_CHAT_RESEARCH_KEYWORDS = (
    "研究", "调研", "综述", "报告", "最新", "进展", "趋势", "现状",
    "对比", "比较", "分析", "论文", "文献", "数据", "案例", "预测",
    "评价", "方法", "技术方案", "原理", "机制", "排行", "市场规模",
    "哪家", "哪个好", "多少钱", "解读", "盘点", "深度", "调研报告",
)
_CHAT_DIRECT_EXACT = {"你好", "在吗", "谢谢", "好的", "嗯", "拜拜", "再见", "嗨", "hello", "hi", "ok"}


def _needs_tools(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if q.lower() in _CHAT_DIRECT_EXACT:
        return False
    if len(q) <= 6:
        return False
    return any(k in q for k in _CHAT_RESEARCH_KEYWORDS)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    if not req.messages:
        return StreamingResponse(iter([_sse({"type": "error", "content": "消息为空"})]),
                                 media_type="text/event-stream")

    def gen():
        # 1. 创建 LLM
        try:
            llm = LLMFactory.create(req.provider, req.model or None)
        except Exception as e:
            yield _sse({"type": "error", "content": f"LLM 初始化失败: {e}"})
            return

        user_input = req.messages[-1].get("content", "") if req.messages else ""

        # 2. 判断是否深度检索
        if req.mode == "chat":
            use_tools = False
        elif req.mode == "deep":
            use_tools = True
        else:
            use_tools = _needs_tools(user_input)

        # 3. 快速问答：直接流式（不带工具 schema，快、省 token）
        if not use_tools:
            history = [m for m in req.messages[:-1] if m.get("content")][-20:]
            messages = (
                [{"role": "system", "content": DIRECT_CHAT_SYSTEM_PROMPT}]
                + history
                + [{"role": "user", "content": user_input}]
            )
            try:
                for chunk in llm.stream_chat(messages, temperature=0.6, max_tokens=1024):
                    if chunk:
                        yield _sse({"type": "delta", "text": chunk})
                yield _sse({"type": "done"})
            except Exception as e:
                yield _sse({"type": "error", "content": f"对话失败: {e}"})
            return

        # 4. 深度检索：ReAct 工具流程（流式推送工具调用与最终答案）
        try:
            from agents.react_agent import ReActAgent, StepType
            from memory.manager import MemoryManager
            from memory.short_term import ShortTermMemory
            from tools.base import TOOL_REGISTRY
            from utils.safety import SafetyGuard
        except Exception as e:
            yield _sse({"type": "error", "content": f"组件加载失败: {e}"})
            return

        tools = []
        for name in req.tools:
            tool = TOOL_REGISTRY.get(name)
            if tool:
                tools.append(tool)
        if req.use_rag:
            vs = get_vector_store()
            if vs:
                try:
                    from tools.knowledge_base_tool import KnowledgeBaseTool
                    tools.append(KnowledgeBaseTool(vs, "default"))
                except Exception:
                    pass

        memory = MemoryManager(ShortTermMemory(max_tokens=6000))
        for msg in req.messages[:-1]:
            if msg.get("content"):
                memory.add(msg["role"], msg["content"])

        agent = ReActAgent(
            llm=llm,
            tools=tools,
            memory=memory,
            safety=SafetyGuard(
                max_iterations=settings.MAX_REACT_ITERATIONS,
                max_token_budget=settings.MAX_TOKEN_BUDGET,
            ),
        )

        final_answer = ""
        try:
            for step in agent.run(user_input):
                if step.type == StepType.ACTION:
                    yield _sse({"type": "tool", "tool": step.tool_name, "args": step.tool_args or {}})
                elif step.type == StepType.OBSERVATION:
                    preview = (step.content or "")[:200].replace("\n", " ")
                    yield _sse({"type": "observation", "text": preview})
                elif step.type in (StepType.FINAL, StepType.ERROR):
                    final_answer = step.content
                    yield _sse({
                        "type": "final",
                        "text": final_answer or "（模型未返回内容）",
                        "error": step.type == StepType.ERROR,
                    })
        except Exception as e:
            yield _sse({"type": "error", "content": f"检索失败: {e}"})
            return
        if not final_answer:
            yield _sse({"type": "final", "text": "未能生成回复，请检查模型配置后重试。", "error": True})
        yield _sse({"type": "done"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 研究任务（后台线程 + 轮询）
# ---------------------------------------------------------------------------
RESEARCH_TASKS: dict[str, dict] = {}


class _BudgetSearchTool:
    """预算包装：限制搜索/学术检索单次返回条数，控制 token 消耗。"""

    def __init__(self, inner, budget: int):
        self._inner = inner
        self._budget = max(1, min(int(budget), 8))

    @property
    def name(self):
        return self._inner.name

    @property
    def description(self):
        return self._inner.description

    @property
    def parameters(self):
        return self._inner.parameters

    def to_openai_schema(self):
        return self._inner.to_openai_schema()

    def execute(self, *args, **kwargs):
        kwargs["max_results"] = min(kwargs.get("max_results") or self._budget, self._budget)
        return self._inner.execute(*args, **kwargs)


def _plan_research(llm, topic: str) -> str:
    """用 LLM 生成检索计划（失败降级空串）。"""
    try:
        resp = llm.chat([
            {"role": "system", "content": "你是研究规划专家，输出简洁、可执行的检索计划。"},
            {"role": "user", "content": (
                "请为以下研究主题生成检索计划，用纯文本输出：\n"
                "1. 子主题（3-5 个，覆盖不同角度）\n"
                "2. 建议检索关键词（3-5 组，中英文均可）\n"
                "3. 建议关注的时间范围\n\n"
                f"研究主题：{topic}"
            )},
        ], temperature=0.3)
        plan = (resp.content or "").strip()
        return plan if len(plan) > 20 else ""
    except Exception as e:
        logger.warning(f"检索计划生成失败，降级为直接检索: {e}")
        return ""


def _extract_keywords(plan: str, limit: int = 3) -> list[str]:
    """从检索计划提取关键词（中英文），用于并行预检索。"""
    if not plan:
        return []
    keywords = []
    for line in plan.splitlines():
        line = line.strip()
        if not line:
            continue
        if "关键词" not in line and "keyword" not in line.lower():
            continue
        if "：" not in line and ":" not in line:
            continue
        kw_part = re.split(r"[:：]", line, maxsplit=1)[-1]
        for item in re.split(r"[，,、;；|/\s]+", kw_part):
            item = item.strip().strip("·•-—")
            if item and len(item) >= 2 and item not in keywords:
                keywords.append(item)
        if len(keywords) >= limit:
            break
    return keywords[:limit]


def _extract_dois(text: str) -> list[str]:
    """从文本提取 DOI（去重，保持出现顺序）。"""
    if not text:
        return []
    dois = []
    for m in re.finditer(r"10\.\d{4,9}/[^\s)\]]+", text):
        doi = m.group(0).rstrip(".,;:，。；：")
        # 截断 URL 查询参数（如 ?version=xxx）
        doi = re.split(r"[?#]", doi)[0]
        if doi not in dois:
            dois.append(doi)
    return dois


def _pre_search(keywords: list[str], tools: dict, max_results: int) -> str:
    """并行预检索：对每个关键词同时跑学术检索 + 网页搜索。"""
    if not keywords:
        return ""

    def _safe_run(tool, kw):
        try:
            return tool.execute(query=kw, max_results=max_results)
        except Exception as e:
            logger.warning(f"预检索失败 {getattr(tool, 'name', '?')} {kw}: {e}")
            return ""

    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = []
        for kw in keywords:
            if tools.get("academic"):
                futures.append(ex.submit(_safe_run, tools["academic"], kw))
            if tools.get("web"):
                futures.append(ex.submit(_safe_run, tools["web"], kw))
        for f in as_completed(futures):
            r = f.result()
            if r and r.strip():
                results.append(r)
    if not results:
        return ""
    parts = []
    for i, r in enumerate(results[:8], 1):
        parts.append(f"[预检索 {i}]\n{r[:1500]}")
    return "\n\n".join(parts)


def _run_research_worker(task_id: str, req: ResearchRequest):
    """后台执行多智能体研究；进度写入任务 dict（前端轮询）。"""
    task = RESEARCH_TASKS[task_id]

    def log(stage: str, msg: str):
        task["stage"] = stage
        task["logs"].append(msg)
        task["updated_at"] = datetime.datetime.now().strftime("%H:%M:%S")

    try:
        log("init", f"🔄 开始研究：{req.topic}")

        # 1. LLM
        provider = req.provider or load_prefs().get("provider") or settings.DEFAULT_LLM_PROVIDER
        model = req.model or load_prefs().get("model", "")
        llm = LLMFactory.create(provider, model or None)
        log("init", f"🤖 模型：{model or provider} ｜ 迭代上限：{req.max_iterations} ｜ 检索条数：{req.max_results}")

        # 2. 工具
        from tools.base import TOOL_REGISTRY
        tools = []
        for name in ["web_search_cn", "academic_search"]:
            tool = TOOL_REGISTRY.get(name)
            if tool:
                tools.append(_BudgetSearchTool(tool, req.max_results))
        scraper = TOOL_REGISTRY.get("web_scraper")
        if scraper:
            tools.append(scraper)

        vs = get_vector_store()
        if vs:
            try:
                from tools.paper_ingest import PaperIngestTool
                tools.append(PaperIngestTool(vs))
            except Exception as e:
                log("init", f"⚠️ 论文入库工具不可用：{e}")

        # 3. RAG
        rag = None
        if req.use_rag and vs:
            try:
                from tools.knowledge_base_tool import KnowledgeBaseTool
                from rag.retriever import Retriever
                tools.append(KnowledgeBaseTool(vs, "default"))
                rag = Retriever(vs, llm, top_k=settings.TOP_K)
            except Exception as e:
                log("init", f"⚠️ RAG 初始化失败：{e}")

        # 4. 检索计划
        log("plan", "🧭 正在生成检索计划...")
        plan = _plan_research(llm, req.topic)
        if plan:
            topic_for_research = f"{req.topic}\n\n【检索计划，请按计划组织检索】\n{plan}"
            log("plan", "✅ 检索计划已生成")
        else:
            topic_for_research = req.topic
            log("plan", "➖ 检索计划生成失败，降级直接检索")

        # 5. 并行预检索
        if req.pre_search:
            keywords = _extract_keywords(plan)
            if keywords:
                log("search", f"⚡ 并行预检索 {len(keywords)} 组关键词...")
                pre_tools = {}
                ac = TOOL_REGISTRY.get("academic_search")
                wb = TOOL_REGISTRY.get("web_search_cn")
                pre_tools["academic"] = _BudgetSearchTool(ac, req.max_results) if ac else None
                pre_tools["web"] = _BudgetSearchTool(wb, req.max_results) if wb else None
                pre_data = _pre_search(keywords, pre_tools, req.max_results)
                if pre_data:
                    topic_for_research += (
                        "\n\n【初始检索资料，研究员可直接使用，在此基础上深挖重点来源】\n"
                        f"{pre_data}"
                    )
                    log("search", "✅ 预检索完成，初始资料已注入")
                else:
                    log("search", "➖ 预检索无结果，继续常规检索")
            else:
                log("search", "➖ 无可用关键词，跳过预检索")

        # 6. 构建并执行工作流
        log("graph", "🔗 正在构建多智能体工作流...")
        from orchestration.graph import build_research_graph, run_research
        graph = build_research_graph(
            llm=llm,
            tools=tools,
            rag=rag,
            knowledge_collection="default",
            max_iterations=req.max_iterations,
            detailed=req.detailed,
        )
        log("graph", "🔍 研究员 → 分析员 → 总结员 → 质检 执行中...")

        result = run_research(graph, topic_for_research)
        summary = result.get("summary", "")
        if not summary:
            raise RuntimeError("工作流未返回报告内容")

        if not _is_report_ok(summary):
            task["status"] = "failed"
            task["error"] = summary[:300]
            log("fail", "⚠️ 研究未生成有效报告（限流或生成失败），结果未保存")
            return

        # 7. 保存报告
        safe_topic = re.sub(r"[\\/:*?\"<>|]", "_", req.topic)[:20]
        fname = f"研究报告_{safe_topic}_{datetime.datetime.now():%Y%m%d_%H%M}.md"
        report_md = (
            f"# 研究报告：{req.topic}\n\n"
            f"> 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M}\n"
            f"> 模型：{model or provider} ｜ 迭代上限：{req.max_iterations} ｜ 检索条数：{req.max_results}\n\n"
            "---\n\n"
            f"{summary}\n"
        )
        with open(os.path.join(REPORT_DIR, fname), "w", encoding="utf-8") as f:
            f.write(report_md)
        task["report"] = report_md
        task["report_file"] = fname
        log("done", f"✅ 报告已生成并保存：{fname}")

        # 8. 自动入库 OA 论文（后台，不影响返回）
        if req.auto_ingest and vs:
            try:
                from tools.paper_ingest import PaperIngestTool
                ingest = PaperIngestTool(vs)
                dois = _extract_dois(summary)
                done = 0
                for doi in dois[:2]:
                    try:
                        r = ingest.execute(doi_or_url=doi)
                        if "已入库" in r:
                            done += 1
                    except Exception:
                        continue
                if done:
                    log("done", f"📥 已自动入库 {done} 篇论文全文")
            except Exception as e:
                log("done", f"⚠️ 自动入库跳过：{e}")

        task["status"] = "done"
    except Exception as e:
        logger.exception("研究任务失败")
        task["status"] = "error"
        task["error"] = str(e)
        log("error", f"❌ 研究失败：{e}")


@app.post("/api/research")
def api_start_research(req: ResearchRequest):
    if not req.topic.strip():
        raise HTTPException(400, "研究主题不能为空")
    task_id = uuid.uuid4().hex[:12]
    RESEARCH_TASKS[task_id] = {
        "id": task_id,
        "status": "running",
        "stage": "starting",
        "logs": [],
        "report": None,
        "report_file": None,
        "error": None,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": "",
    }
    threading.Thread(target=_run_research_worker, args=(task_id, req), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/research/{task_id}")
def api_research_status(task_id: str):
    task = RESEARCH_TASKS.get(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return {
        "status": task["status"],
        "stage": task["stage"],
        "logs": task["logs"],
        "report": task.get("report"),
        "report_file": task.get("report_file"),
        "error": task.get("error"),
        "created_at": task["created_at"],
        "updated_at": task.get("updated_at", ""),
    }


# ---------------------------------------------------------------------------
# 报告历史（列表 / 下载 / 删除）
# ---------------------------------------------------------------------------
@app.get("/api/reports")
def api_list_reports():
    try:
        files = sorted(
            [f for f in os.listdir(REPORT_DIR) if f.endswith(".md")],
            reverse=True,
        )
    except OSError:
        files = []
    reports = []
    for fname in files:
        fpath = os.path.join(REPORT_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                head = f.read(2000)
        except OSError:
            continue
        if not _is_report_ok(head):
            continue  # 生成失败的报告不显示
        reports.append({"name": fname})
    return {"reports": reports}


@app.get("/api/reports/{name}")
def api_get_report(name: str, format: str = "md"):
    fpath = _safe_report_path(name)
    if format == "docx":
        try:
            from utils.md_to_docx import md_to_docx
            content = open(fpath, encoding="utf-8").read()
            buf = md_to_docx(content)
            from fastapi.responses import Response
            return Response(
                content=buf,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{name.replace(".md", ".docx")}"'},
            )
        except Exception as e:
            raise HTTPException(500, f"Word 导出失败: {e}")
    return FileResponse(fpath, media_type="text/markdown",
                        filename=name)


@app.delete("/api/reports/{name}")
def api_delete_report(name: str):
    fpath = _safe_report_path(name)
    try:
        os.remove(fpath)
        return {"ok": True, "deleted": name}
    except OSError as e:
        raise HTTPException(500, f"删除失败: {e}")


# ---------------------------------------------------------------------------
# 知识库
# ---------------------------------------------------------------------------
@app.get("/api/kb")
def api_kb_list():
    vs = get_vector_store()
    if vs is None:
        return {"available": False, "collections": [], "reason": "embedding 未配置"}
    cols = vs.list_collections()
    return {
        "available": True,
        "collections": [
            {"name": c, "count": vs.get_collection_info(c).get("count", 0)}
            for c in cols
        ],
    }


@app.post("/api/kb/collections")
def api_kb_create(req: KbCreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用（请先配置 Embedding）")
    vs.get_or_create_collection(name)
    return {"ok": True, "name": name}


@app.delete("/api/kb/collections/{name}")
def api_kb_delete_collection(name: str):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用")
    vs.delete_collection(name)
    return {"ok": True, "deleted": name}


@app.post("/api/kb/upload")
async def api_kb_upload(collection: str = Form("default"), files: list[UploadFile] = File(...)):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用（请先配置 Embedding）")
    from rag.document_loader import DocumentLoader
    from rag.text_splitter import TextSplitter

    loader = DocumentLoader()
    splitter = TextSplitter(chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
    results = []
    total_chunks = 0
    for uf in files:
        name = uf.filename or "unknown"
        ext = os.path.splitext(name)[1]
        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(await uf.read())
            docs = loader.load_file(tmp_path)
            if docs:
                chunks = splitter.split_documents(docs)
                vs.add_documents(collection, chunks)
                total_chunks += len(chunks)
                results.append({"name": name, "chunks": len(chunks), "ok": True})
            else:
                results.append({"name": name, "chunks": 0, "ok": False, "error": "未提取到内容"})
        except Exception as e:
            results.append({"name": name, "chunks": 0, "ok": False, "error": str(e)})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return {"ok": True, "collection": collection, "total_chunks": total_chunks, "results": results}


@app.post("/api/kb/url")
def api_kb_url(req: KbUrlRequest):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用（请先配置 Embedding）")
    from rag.document_loader import DocumentLoader
    from rag.text_splitter import TextSplitter

    loader = DocumentLoader()
    splitter = TextSplitter(chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
    try:
        docs = loader.load_url(req.url)
        if not docs:
            return {"ok": False, "error": "未提取到内容"}
        chunks = splitter.split_documents(docs)
        vs.add_documents(req.collection, chunks)
        return {"ok": True, "chunks": len(chunks), "collection": req.collection}
    except Exception as e:
        raise HTTPException(500, f"抓取失败: {e}")


@app.post("/api/kb/search")
def api_kb_search(req: KbSearchRequest):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用")
    results = vs.search(req.collection, req.query, k=max(1, min(int(req.k), 10)))
    return {"results": results}


# ---------------------------------------------------------------------------
# 静态前端（最后挂载，避免拦截 API）
# ---------------------------------------------------------------------------
_WEB_DIR = os.path.join(BASE_DIR, "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import threading
    import webbrowser

    # 服务器就绪后自动打开浏览器（延迟 1.6s，只开一个标签页）
    def _open_browser():
        try:
            webbrowser.open("http://localhost:8501")
        except Exception:
            pass

    threading.Timer(1.6, _open_browser).start()

    import uvicorn
    print("=" * 50)
    print("  研究助手 Web 服务  v2.0")
    print("  浏览器访问: http://localhost:8501")
    print("  停止服务: 本窗口按 Ctrl+C")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8501, log_level="warning")
