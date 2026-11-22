"""UI 偏好持久化：记住侧边栏选择的提供商/模型与研究默认参数，重启后自动恢复。"""
import copy
import json
import os

from utils.atomic_file import atomic_write
from utils.config_io import transaction
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


@transaction
def load_prefs() -> dict:
    """读取 UI 偏好；文件不存在或损坏时返回默认值。"""
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        result = copy.deepcopy(_DEFAULTS)
        if not isinstance(data, dict):
            return result
        for key, default in _DEFAULTS.items():
            value = data.get(key)
            if type(value) is not type(default):
                continue
            if key == "research":
                result[key].update({
                    k: v for k, v in value.items()
                    if k in default and type(v) is type(default[k])
                })
            else:
                result[key] = value
        return result
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(_DEFAULTS)


@transaction
def save_prefs(provider: str, model: str, custom_models: dict | None = None,
               research: dict | None = None) -> None:
    """保存 UI 偏好（幂等，仅配置变化时调用）。"""
    current = load_prefs()
    current["provider"] = provider
    current["model"] = model
    if custom_models is not None:
        current["custom_models"] = dict(custom_models)
    if research is not None:
        current["research"].update({
            k: v for k, v in research.items() if k in _DEFAULTS["research"]
        })
    atomic_write(PREFS_PATH, json.dumps(current, ensure_ascii=False, indent=2))
