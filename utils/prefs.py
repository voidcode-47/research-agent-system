# -*- coding: utf-8 -*-
"""UI 偏好持久化：记住侧边栏选择的提供商/模型与研究默认参数，重启后自动恢复。"""
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
        "max_results": 8,
        "detailed": True,
    },
}


def load_prefs() -> dict:
    """读取 UI 偏好；文件不存在或损坏时返回默认值。"""
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        result = copy.deepcopy(_DEFAULTS)
        result.update({k: v for k, v in data.items() if k in _DEFAULTS})
        return result
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(_DEFAULTS)


def save_prefs(provider: str, model: str, custom_models: dict | None = None,
               research: dict | None = None) -> None:
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
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"UI 偏好保存失败: {e}")
