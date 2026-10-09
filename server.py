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

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
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

# 单个上传文件字节上限（防止整文件读入内存 / 磁盘被写满）
MAX_UPLOAD_MB = 50
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# 已完成研究任务的保留上限（防止 RESEARCH_TASKS 无界增长）
MAX_RESEARCH_TASKS = 200

# 云提供商 → (.env 的 API Key 变量, .env 的 Base URL 变量, 官方默认 Base URL)
# 用于前端填写/保存各云厂商的 Key 与 URL（key 不再内置在项目文件里）
CLOUD_ENV_MAP: dict[str, tuple[str, str, str]] = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
    "zhipu": ("ZHIPU_API_KEY", "ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
    "qwen": ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
}


def _is_report_ok(text: str) -> bool:
    """判断报告内容是否为有效生成结果（非限流/调用失败/降级产物）。"""
    if not text:
        return False
    return not any(marker in text for marker in _FAIL_MARKERS)


# 模型池上限：防止前端异常把 prefs 写爆
MAX_MODEL_POOL = 12


def _normalize_model_pool(raw: list) -> list[dict]:
    """规范化场景模型池：丢弃非法/重复项，保序，限量。

    非法提供商静默丢弃（而非 400）：池操作由前端整体提交，
    客户端状态与服务器状态不一致时以服务器侧净化为准。
    """
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        p = str(it.get("provider") or "").strip().lower()
        m = str(it.get("model") or "").strip()
        if not p or p not in PROVIDER_CONFIG:
            continue
        key = (p, m)
        if key in seen:
            continue
        seen.add(key)
        pool.append({"provider": p, "model": m})
        if len(pool) >= MAX_MODEL_POOL:
            break
    return pool


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
_vs_lock = threading.Lock()
# 最近一次向量库初始化失败的详细原因（供 API 向用户给出具体配置指引）
_vs_last_error = "embedding 未配置"


def get_vector_store():
    """获取 VectorStore 单例；embedding 不可用返回 None。

    加锁：EmbeddingProvider 初始化会做多次同步网络探测，并发请求若同时
    进入会重复构建并互相覆盖单例。
    """
    global _vs_singleton, _vs_last_fail_ts, _vs_last_error
    if _vs_singleton is not None:
        return _vs_singleton
    with _vs_lock:
        # 双检：等待锁期间可能已被其他线程构建完成
        if _vs_singleton is not None:
            return _vs_singleton
        if time.monotonic() - _vs_last_fail_ts < _VS_FAIL_COOLDOWN:
            return None
        try:
            from tools.vector_store import VectorStore
            from llm.embedding import EmbeddingProvider
            # 知识库场景模型：设置页/左侧「知识库」界面配置的 embedding 提供商+模型
            kb_scene = load_prefs().get("scene_models", {}).get("kb", {}) or {}
            ep = EmbeddingProvider(
                provider=kb_scene.get("provider") or None,
                model=kb_scene.get("model") or None,
            )
            _vs_singleton = VectorStore(
                persist_dir=settings.CHROMA_PERSIST_DIR,
                embedding_fn=ep,
            )
            _vs_last_error = ""
            return _vs_singleton
        except Exception as e:
            logger.warning(f"VectorStore 初始化失败: {e}")
            _vs_last_error = str(e)
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
    # 云提供商（deepseek/zhipu/qwen）的临时 Key 与 Base URL，由前端填写
    cloud_api_key: Optional[str] = None
    cloud_base_url: Optional[str] = None
    # 设置页的研究默认参数
    research_max_iterations: Optional[int] = None
    research_max_results: Optional[int] = None
    research_detailed: Optional[bool] = None
    research_pre_search: Optional[bool] = None
    research_auto_ingest: Optional[bool] = None
    research_use_rag: Optional[bool] = None
    # 分场景模型（左侧模型配置随界面切换，设置页可集中配置）
    chat_provider: Optional[str] = None
    chat_model: Optional[str] = None
    research_provider: Optional[str] = None
    research_model: Optional[str] = None
    kb_provider: Optional[str] = None
    kb_model: Optional[str] = None
    # 分场景模型池：同一功能可配置多个 (provider, model)，整体替换该场景的池
    chat_models: Optional[list[dict]] = None
    research_models: Optional[list[dict]] = None
    kb_models: Optional[list[dict]] = None
    # 本地服务 Base URL（设置页「模型提供商」直接修改）
    ollama_base_url: Optional[str] = None
    lmstudio_base_url: Optional[str] = None


class HealthCheckRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    # 前端刚填写的临时 Key / URL（未保存时也能直接测试连接）
    api_key: Optional[str] = None
    base_url: Optional[str] = None


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
    cloud_status = {}
    for p, (key_env, base_env, default_base) in CLOUD_ENV_MAP.items():
        cloud_status[p] = {
            "has_key": bool(getattr(settings, key_env, "")),
            "base_url": getattr(settings, base_env, "") or default_base,
        }
    return {
        "providers": list_providers(),
        "labels": {k: v["label"] for k, v in PROVIDER_CONFIG.items()},
        "current_provider": prefs.get("provider"),
        "current_model": prefs.get("model"),
        "custom_models": prefs.get("custom_models", {}),
        "cloud": cloud_status,
        "custom_api": {
            "base_url": settings.CUSTOM_API_BASE_URL,
            "api_key": bool(settings.CUSTOM_API_KEY),
            "model": settings.CUSTOM_API_MODEL,
        },
        "local_api": {
            "ollama": settings.OLLAMA_BASE_URL,
            "lmstudio": settings.LMSTUDIO_BASE_URL,
        },
        "research_prefs": prefs.get("research", {}),
        "scene_models": prefs.get("scene_models", {}),
    }


@app.get("/api/models")
def api_models(provider: str, kind: str = "llm"):
    try:
        models = LLMFactory.get_models(provider, kind=kind)
        return {"models": models, "provider": provider, "kind": kind}
    except Exception as e:
        return {"models": [], "provider": provider, "kind": kind, "error": str(e)}


@app.post("/api/health-check")
def api_health_check(req: HealthCheckRequest):
    provider = req.provider or load_prefs().get("provider") or settings.DEFAULT_LLM_PROVIDER
    model = req.model
    return LLMFactory.health_check(provider, model, api_key=req.api_key, base_url=req.base_url)


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest):
    prefs = load_prefs()
    provider = req.provider or prefs.get("provider") or settings.DEFAULT_LLM_PROVIDER
    # 提前校验提供商：否则非法值会被写进 ui_prefs.json，
    # 直到下次对话/研究时才在 LLMFactory 里抛错，报错点离操作太远
    if provider not in PROVIDER_CONFIG:
        raise HTTPException(400, f"未知提供商: {provider}，可选: {list(PROVIDER_CONFIG.keys())}")
    # custom_models[provider] 记录该提供商下用户手填的模型名（静态列表里没有的）
    custom_models = dict(prefs.get("custom_models", {}))
    if req.custom_model is not None:
        typed = req.custom_model.strip()
        if typed:
            custom_models[provider] = typed
        else:
            custom_models.pop(provider, None)

    model = req.model if req.model is not None else prefs.get("model", "")

    # 自定义 API 的 Base URL / Key / 模型名只在选定 custom 提供商时写入 .env。
    # 否则在别的提供商页面点保存，会把已配置好的自定义网关整段覆盖掉。
    if provider == "custom" and (
        req.custom_base_url is not None
        or req.custom_api_key is not None
        or req.custom_model is not None
    ):
        updates = {}
        if req.custom_base_url is not None:
            updates["CUSTOM_API_BASE_URL"] = req.custom_base_url.strip()
            settings.CUSTOM_API_BASE_URL = updates["CUSTOM_API_BASE_URL"]
        if req.custom_api_key is not None:
            key = req.custom_api_key.strip()
            # 空串视为「不修改」：前端不回显已保存的 Key，
            # 用户填完 Base URL/模型后直接点保存不应该把 Key 清空
            if key:
                updates["CUSTOM_API_KEY"] = key
                settings.CUSTOM_API_KEY = key
        if req.custom_model is not None:
            updates["CUSTOM_API_MODEL"] = req.custom_model.strip()
            settings.CUSTOM_API_MODEL = updates["CUSTOM_API_MODEL"]
        if updates:
            update_env(updates)

    # 云提供商（deepseek/zhipu/qwen）：Key / Base URL 由前端填写后写入 .env 并热更新
    if provider in CLOUD_ENV_MAP:
        key_env, base_env, _default = CLOUD_ENV_MAP[provider]
        updates = {}
        if req.cloud_api_key is not None:
            key = req.cloud_api_key.strip()
            if key:  # 留空视为「不修改」，避免误清空已保存的 Key
                updates[key_env] = key
                setattr(settings, key_env, key)
        if req.cloud_base_url is not None:
            url = req.cloud_base_url.strip()
            if url:
                updates[base_env] = url
                setattr(settings, base_env, url)
        if updates:
            update_env(updates)

    # 本地服务（Ollama / LM Studio）：设置页「模型提供商」直接改服务地址
    local_url_updates = {}
    if req.ollama_base_url is not None:
        url = req.ollama_base_url.strip().rstrip("/")
        if url:
            local_url_updates["OLLAMA_BASE_URL"] = url
            settings.OLLAMA_BASE_URL = url
    if req.lmstudio_base_url is not None:
        url = req.lmstudio_base_url.strip().rstrip("/")
        if url:
            local_url_updates["LMSTUDIO_BASE_URL"] = url
            settings.LMSTUDIO_BASE_URL = url
    if local_url_updates:
        update_env(local_url_updates)

    # 设置页研究默认参数（写入 ui_prefs.json）
    research_updates = {}
    if req.research_max_iterations is not None:
        research_updates["max_iterations"] = max(3, min(15, req.research_max_iterations))
    if req.research_max_results is not None:
        research_updates["max_results"] = max(2, min(10, req.research_max_results))
    if req.research_detailed is not None:
        research_updates["detailed"] = req.research_detailed
    if req.research_pre_search is not None:
        research_updates["pre_search"] = req.research_pre_search
    if req.research_auto_ingest is not None:
        research_updates["auto_ingest"] = req.research_auto_ingest
    if req.research_use_rag is not None:
        research_updates["use_rag"] = req.research_use_rag
    if research_updates:
        save_prefs(provider, model, custom_models, research=research_updates)
    else:
        save_prefs(provider, model, custom_models)

    # 分场景模型（对话/研究/知识库）保存：某场景的 provider/model/pool 任一非空才更新。
    # 模型池整体替换；替换后若原激活模型不在池中，顺延为池中第一个；池为空则清空激活。
    scene_updates = {}
    scene_fields = {
        "chat": (req.chat_provider, req.chat_model, req.chat_models),
        "research": (req.research_provider, req.research_model, req.research_models),
        "kb": (req.kb_provider, req.kb_model, req.kb_models),
    }
    for key, (p, m, pool_raw) in scene_fields.items():
        if p is None and m is None and pool_raw is None:
            continue  # 该场景未提供任何值 → 不更新
        if p is not None and (p or "").strip() and (p or "").strip() not in PROVIDER_CONFIG:
            raise HTTPException(400, f"未知提供商: {p.strip()}，可选: {list(PROVIDER_CONFIG.keys())}")
        cur = (prefs.get("scene_models") or {}).get(key) or {}
        entry = {"provider": cur.get("provider", ""), "model": cur.get("model", "")}
        if p is not None:
            entry["provider"] = (p or "").strip()
        if m is not None:
            entry["model"] = (m or "").strip()
        if pool_raw is not None:
            pool = _normalize_model_pool(pool_raw)
            entry["models"] = pool
            if not pool:
                entry["provider"] = ""
                entry["model"] = ""
            elif not any(e["provider"] == entry["provider"] and e["model"] == entry["model"]
                         for e in pool):
                entry["provider"] = pool[0]["provider"]
                entry["model"] = pool[0]["model"]
        scene_updates[key] = entry
    if scene_updates:
        save_prefs(provider, model, custom_models, scene_models=scene_updates)

    return {"ok": True, "provider": provider, "model": model}


# ---------------------------------------------------------------------------
# 聊天（SSE 流式）
# ---------------------------------------------------------------------------
_CHAT_RESEARCH_KEYWORDS = (
    "研究", "调研", "综述", "报告", "最新", "进展", "趋势", "现状",
    "对比", "比较", "分析", "论文", "文献", "数据", "案例", "预测",
    "评价", "方法", "技术方案", "原理", "机制", "排行", "市场规模",
    "哪家", "哪个好", "多少钱", "解读", "盘点", "深度", "调研报告",
    "新闻", "热点", "事件", "概念", "知识", "区别", "差异",
)
_CHAT_DIRECT_EXACT = {"你好", "在吗", "谢谢", "好的", "嗯", "拜拜", "再见", "嗨", "hello", "hi", "ok"}


def _needs_tools(query: str) -> bool:
    """自动模式的分流判断：研究/事实型问题 → 深度检索；闲聊/短句 → 直接回答。

    判断顺序：寒暄词 → 研究关键词 → 个人求助式短句 → 疑问长句。
    目的是少漏判（研究问题被当闲聊，导致回答质量差）也少误判（闲聊被拉去检索，白耗 token）。
    """
    q = (query or "").strip()
    if not q:
        return False
    if q.lower() in _CHAT_DIRECT_EXACT:
        return False
    if len(q) <= 6:
        return False
    # 研究/检索类关键词命中 → 深度检索
    if any(k in q for k in _CHAT_RESEARCH_KEYWORDS):
        return True
    # 个人求助/操作类短句（调试报错、日常求助）→ 直接回答，别拉去检索
    if q.startswith(("帮我", "给我", "你能", "可以", "请问", "怎么设置", "怎么解决", "如何安装", "怎么装")):
        return False
    # 事实/时事查询：时间词或行情/排名/新闻信号 + 疑问长句 → 需要最新/权威信息，走深度检索
    fact_signal = ("昨天", "今天", "今年", "去年", "明年", "最近", "现在", "几号", "多少",
                   "价格", "行情", "市场", "排名", "榜单", "名单", "最新", "热点", "新闻", "事件")
    if (
        len(q) >= 8
        and any(k in q for k in fact_signal)
        and any(k in q for k in ("什么", "为什么", "怎么", "如何", "哪些", "区别", "差异", "是否", "怎样"))
    ):
        return True
    return False


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    if not req.messages:
        return StreamingResponse(iter([_sse({"type": "error", "content": "消息为空"})]),
                                 media_type="text/event-stream")

    def gen():
        # 1. 创建 LLM：对话用「智能对话」场景配置的模型
        try:
            scene = load_prefs().get("scene_models", {}).get("chat", {}) or {}
            provider = req.provider or scene.get("provider") or settings.DEFAULT_LLM_PROVIDER
            model = req.model or scene.get("model") or None
            llm = LLMFactory.create(provider, model)
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

_TERMINAL_TASK_STATUS = ("done", "error", "failed")


def _prune_research_tasks() -> None:
    """任务表超上限时，按创建时间清理最旧的已结束任务。"""
    if len(RESEARCH_TASKS) <= MAX_RESEARCH_TASKS:
        return
    stale = sorted(
        (
            (tid, t.get("created_at") or "")
            for tid, t in RESEARCH_TASKS.items()
            if t.get("status") in _TERMINAL_TASK_STATUS
        ),
        key=lambda kv: kv[1],
    )
    for tid, _ in stale[: len(RESEARCH_TASKS) - MAX_RESEARCH_TASKS]:
        RESEARCH_TASKS.pop(tid, None)


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
        try:
            requested = int(kwargs.get("max_results") or self._budget)
        except (TypeError, ValueError):
            requested = self._budget
        kwargs["max_results"] = max(1, min(requested, self._budget))
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

        # 1. LLM：研究用「多智能体研究」场景配置的模型
        scene = load_prefs().get("scene_models", {}).get("research", {}) or {}
        provider = req.provider or scene.get("provider") or settings.DEFAULT_LLM_PROVIDER
        model = req.model or scene.get("model") or ""
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
    _prune_research_tasks()
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
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
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
# ---------------------------------------------------------------------------
# 知识库：Chroma 集合名只允许 [a-zA-Z0-9._-] 且 3-512 字符，
# 用户可能输入中文名或短名（如 "01"）→ 内部用合法名，界面显示用户起的名字
# ---------------------------------------------------------------------------
import hashlib
import json

_KB_NAMES_PATH = os.path.join(BASE_DIR, "data", "kb_names.json")


def _load_kb_names() -> dict:
    try:
        with open(_KB_NAMES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_kb_names(names: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_KB_NAMES_PATH), exist_ok=True)
        with open(_KB_NAMES_PATH, "w", encoding="utf-8") as f:
            json.dump(names, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"知识库名称映射保存失败: {e}")


def _kb_display(internal: str) -> str:
    """内部合法名 → 用户显示名（无映射则原样返回，兼容旧集合 default 等）。"""
    return _load_kb_names().get(internal, internal)


def _kb_internal(display_or_internal: str) -> str:
    """用户显示名或内部名 → Chroma 内部合法名。"""
    names = _load_kb_names()
    for internal, display in names.items():
        if display == display_or_internal:
            return internal
    return display_or_internal


@app.get("/api/kb")
def api_kb_list():
    vs = get_vector_store()
    if vs is None:
        return {"available": False, "collections": [], "reason": _vs_last_error}
    cols = vs.list_collections()
    return {
        "available": True,
        "collections": [
            {"name": _kb_display(c), "internal": c, "count": vs.get_collection_info(c).get("count", 0)}
            for c in cols
        ],
    }


@app.post("/api/kb/collections")
def api_kb_create(req: KbCreateRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if len(name) > 64:
        raise HTTPException(400, "名称过长（最多 64 字符）")
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(
            400,
            f"向量存储不可用：{_vs_last_error}。"
            "请在设置页「模型提供商」中填写 embedding 提供商的 Key（智谱/通义），"
            "或在 Ollama / LM Studio 中加载 embedding 模型后重启服务。",
        )
    names = _load_kb_names()
    # 同名已存在 → 直接返回（幂等）
    for internal, display in names.items():
        if display == name:
            return {"ok": True, "name": name, "internal": internal}
    # Chroma 集合名限制：仅字母数字 ._-，3-512 字符，首尾须为字母数字
    internal = "kb_" + hashlib.md5((name + str(time.time())).encode("utf-8")).hexdigest()[:10]
    try:
        vs.get_or_create_collection(internal)
    except Exception as e:
        logger.warning(f"创建知识库失败 {name}: {e}")
        raise HTTPException(400, f"创建知识库「{name}」失败：{e}")
    names[internal] = name
    _save_kb_names(names)
    return {"ok": True, "name": name, "internal": internal}


@app.delete("/api/kb/collections/{name}")
def api_kb_delete_collection(name: str):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用")
    internal = _kb_internal(name)
    vs.delete_collection(internal)
    names = _load_kb_names()
    if internal in names:
        names.pop(internal, None)
        _save_kb_names(names)
    return {"ok": True, "deleted": name}


@app.post("/api/kb/upload")
def api_kb_upload(collection: str = Form("default"), files: list[UploadFile] = File(...)):
    """上传并入库文件。

    同步 def：FastAPI 会在线程池执行，避免 embedding 网络调用与磁盘 IO
    阻塞事件循环（否则上传期间所有并发请求都会卡住）。
    """
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用（请先配置 Embedding）")
    from rag.document_loader import DocumentLoader
    from rag.text_splitter import TextSplitter

    loader = DocumentLoader()
    splitter = TextSplitter(chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
    results = []
    total_chunks = 0
    collection = _kb_internal(collection)
    for uf in files:
        name = uf.filename or "unknown"
        ext = os.path.splitext(name)[1]
        import tempfile
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        try:
            # 流式落盘 + 硬上限，避免整文件读入内存
            written = 0
            with open(tmp_path, "wb") as f:
                while chunk := uf.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise ValueError(f"文件 {name} 超过 {MAX_UPLOAD_MB}MB 上限")
                    f.write(chunk)
            if written == 0:
                raise ValueError(f"文件为空: {name}")
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
    return {"ok": True, "collection": _kb_display(collection), "total_chunks": total_chunks, "results": results}


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
        vs.add_documents(_kb_internal(req.collection), chunks)
        return {"ok": True, "chunks": len(chunks), "collection": req.collection}
    except Exception as e:
        raise HTTPException(500, f"抓取失败: {e}")


@app.post("/api/kb/search")
def api_kb_search(req: KbSearchRequest):
    vs = get_vector_store()
    if vs is None:
        raise HTTPException(400, "向量存储不可用")
    results = vs.search(_kb_internal(req.collection), req.query, k=max(1, min(int(req.k), 10)))
    return {"results": results}


# ---------------------------------------------------------------------------
# 静态前端（最后挂载，避免拦截 API）
# ---------------------------------------------------------------------------
_WEB_DIR = os.path.join(BASE_DIR, "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """静态页面/脚本不缓存：浏览器每次重新验证，避免长期使用旧版前端。

    注意：仅对非 API 路径设置；API（/api/...）保持原有行为。
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


if __name__ == "__main__":
    import threading
    import time
    import socket
    import webbrowser
    import uvicorn

    print("=" * 50)
    print("  研究助手 Web 服务  v2.0")
    print("  浏览器访问: http://localhost:8501")
    print("  停止服务: 本窗口按 Ctrl+C")
    print("=" * 50)

    def _port_in_use(port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                return s.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    # 启动前检查端口：已被占用说明旧实例还在运行，不再重复启动
    if _port_in_use(8501):
        print("[提示] 8501 端口已有服务在运行（可能上次双击启动过），不再重复启动。")
        try:
            webbrowser.open("http://localhost:8501")
            print("        已打开浏览器访问现有实例，按 Enter 关闭本窗口。")
        except Exception:
            pass
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass
        raise SystemExit(0)

    config = uvicorn.Config(app, host="127.0.0.1", port=8501, log_level="warning")
    server = uvicorn.Server(config)

    # 服务在后台线程运行，主线程等端口真正就绪后再开浏览器
    # （冷启动要导入 chromadb 等，可能需要数秒；过早打开浏览器会拿到空白页）
    threading.Thread(target=server.run, daemon=True).start()

    ready = False
    for _ in range(120):  # 最多等 60 秒
        time.sleep(0.5)
        if _port_in_use(8501):
            ready = True
            break

    if ready:
        try:
            webbrowser.open("http://localhost:8501")
        except Exception:
            pass
        print("  服务已就绪，浏览器已打开 http://localhost:8501")
    else:
        print("[错误] 服务启动超时，请检查上方日志。")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True
        print("\n正在停止服务...")
