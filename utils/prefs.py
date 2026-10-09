# -*- coding: utf-8 -*-
"""UI 偏好持久化：记住侧边栏选择的提供商/模型，重启后自动恢复。"""
import copy
import json
import os

from utils.logger import get_logger

logger = get_logger(__name__)

_PREFS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PREFS_PATH = os.path.join(_PREFS_DIR, "ui_prefs.json")

_DEFAULTS = {
    "provider": "",
    "model": "",
    "custom_models": {},  # provider -> 自定义模型名
    # 研究页默认参数（设置页可修改，研究页初始化时读取）
    "research": {
        "max_iterations": 8,
        "max_results": 5,
        "detailed": True,
        "pre_search": True,
        "auto_ingest": True,
        "use_rag": False,
    },
    # 分场景模型：对话 / 研究 / 知识库(embedding) 各用各的，
    # 左侧模型配置随当前界面切换，避免"模型串台"。
    # 每个场景另有 models 池：同一功能可配置多个 (provider, model)，切换"当前"用
    "scene_models": {
        "chat": {"provider": "", "model": "", "models": []},
        "research": {"provider": "", "model": "", "models": []},
        "kb": {"provider": "", "model": "", "models": []},
    },
}

# embedding 模型名关键词：识别向量模型（用于旧配置迁移时避免 LLM 场景被赋向量模型）
_EMBED_KW = ("embed", "bge", "e5-", "gte", "minilm", "sentence")


def _is_embedding_model(model: str) -> bool:
    return any(k in (model or "").lower() for k in _EMBED_KW)


def _norm_pool(models) -> list[dict]:
    """规范化模型池：只保留 {provider, model} 结构，按 (provider, model) 去重保序。"""
    if not isinstance(models, list):
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in models:
        if not isinstance(it, dict):
            continue
        p = str(it.get("provider") or "").strip()
        m = str(it.get("model") or "").strip()
        key = (p, m)
        if not p or key in seen:
            continue
        seen.add(key)
        out.append({"provider": p, "model": m})
    return out


def load_prefs() -> dict:
    """读取 UI 偏好；文件不存在或损坏时返回默认值。

    旧版本只有全局 provider/model（且可能误存了 embedding 模型当 LLM）：
    迁移到 scene_models 时，对话/研究场景若旧模型是向量模型则置空
    （避免 LLM 调用向量模型报错），知识库场景保留原值（embedding 正好该用）。
    """
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # 必须深拷贝：迁移循环会改写 scene_models 内层 dict，
        # 浅拷贝会把 module 级 _DEFAULTS 一起污染（跨调用泄漏上一次的值）
        result = copy.deepcopy(_DEFAULTS)
        result.update({k: v for k, v in data.items() if k in _DEFAULTS})
        # 迁移：旧版本无 scene_models，或某场景未配置时，
        # 用全局 provider/model 逐场景兜底（避免空配置直接回退默认提供商）。
        # 仅对真正的旧版配置（文件里没有 scene_models 键）生效：
        # 新格式配置中场景被显式清空（provider=""）应保持原样，不得被全局值复活
        old_p = result.get("provider", "")
        old_m = result.get("model", "")
        legacy = not isinstance(data.get("scene_models"), dict)
        sm = result.get("scene_models")
        if not isinstance(sm, dict):
            sm = copy.deepcopy(_DEFAULTS["scene_models"])
        for key in ("chat", "research", "kb"):
            entry = sm.get(key)
            if not isinstance(entry, dict):
                entry = copy.deepcopy(_DEFAULTS["scene_models"][key])
            if legacy:
                if not entry.get("provider") and old_p:
                    entry["provider"] = old_p
                if not entry.get("model") and old_m:
                    # 对话/研究是 LLM：旧模型若是向量模型则置空（等用户选），
                    # 知识库是 embedding：原值（向量模型）正好可用
                    if key == "kb" or not _is_embedding_model(old_m):
                        entry["model"] = old_m
            # 迁移：旧配置无 models 池 → 用当前激活的 provider/model 播种，
            # 保证升级后侧边栏「当前功能模型列表」立刻有内容可选
            pool = _norm_pool(entry.get("models"))
            if not pool and entry.get("provider"):
                pool = [{"provider": entry["provider"], "model": entry.get("model", "")}]
            entry["models"] = pool
            sm[key] = entry
        result["scene_models"] = sm
        return result
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(_DEFAULTS)


def save_prefs(provider: str, model: str, custom_models: dict | None = None,
               research: dict | None = None,
               scene_models: dict | None = None) -> None:
    """保存 UI 偏好（幂等，仅配置变化时调用）。"""
    try:
        os.makedirs(_PREFS_DIR, exist_ok=True)
        current = load_prefs()
        current["provider"] = provider
        current["model"] = model
        if custom_models is not None:
            current["custom_models"] = dict(custom_models)
        if research is not None:
            merged = dict(_DEFAULTS["research"])
            merged.update({k: v for k, v in research.items() if k in merged})
            current["research"] = merged
        if scene_models is not None:
            # 以「当前已存值」为合并基线：未提供的字段保持原值，
            # 从 _DEFAULTS 起步会把已存的池/激活项静默重置
            cur_scenes = current.get("scene_models") or {}
            base = {}
            for key in ("chat", "research", "kb"):
                cur = cur_scenes.get(key)
                base[key] = (copy.deepcopy(cur) if isinstance(cur, dict)
                             else dict(_DEFAULTS["scene_models"][key]))
                val = scene_models.get(key)
                if val and isinstance(val, dict):
                    merged = base[key]
                    for f in ("provider", "model"):
                        # 空串 = 显式清空；None = 未提供，保持原值
                        if val.get(f) is not None:
                            merged[f] = str(val[f]).strip()
                    if "models" in val:
                        merged["models"] = _norm_pool(val.get("models"))
                    base[key] = merged
            current["scene_models"] = base
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"UI 偏好保存失败: {e}")
