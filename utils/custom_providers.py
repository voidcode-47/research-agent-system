"""持久化自定义 LLM 服务元数据；API 密钥单独保存在本机 .env 中。"""
import json
import os
import re
import secrets
import threading
from pathlib import Path

from config.settings import settings
from utils.env_file import update_env

BASE_DIR = Path(__file__).resolve().parent.parent
CUSTOM_PROVIDERS_PATH = str(BASE_DIR / "data" / "custom_providers.json")
ENV_PATH = str(BASE_DIR / ".env")
SUPPORTED_PROTOCOLS = {"openai", "anthropic", "gemini"}
_lock = threading.RLock()


def _read_services() -> list[dict]:
    try:
        with open(CUSTOM_PROVIDERS_PATH, encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError):
        return []
    records = data.get("services") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return []
    return [
        item for item in records
        if isinstance(item, dict) and re.fullmatch(r"[a-z0-9_-]{1,40}", str(item.get("id", "")))
    ]


def _write_services(services: list[dict]) -> None:
    path = Path(CUSTOM_PROVIDERS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"services": services}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _api_key_env(service_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9]", "_", service_id).upper()
    return f"CUSTOM_API_KEY_{safe_id}"


def get_api_key(service_id: str) -> str:
    key_name = "CUSTOM_API_KEY" if service_id == "legacy" else _api_key_env(service_id)
    if service_id == "legacy":
        return str(getattr(settings, key_name, "") or "")
    if os.environ.get(key_name):
        return os.environ[key_name]
    try:
        from dotenv import dotenv_values
        return str(dotenv_values(ENV_PATH).get(key_name) or "")
    except Exception:
        return ""


def _public_service(service: dict) -> dict:
    return {
        **service,
        "has_api_key": bool(get_api_key(str(service.get("id", "")))),
    }


def list_services() -> list[dict]:
    with _lock:
        return [_public_service(item) for item in _read_services()]


def get_service(service_id: str) -> dict | None:
    with _lock:
        return next((dict(item) for item in _read_services() if item["id"] == service_id), None)


def get_service_config(provider: str) -> dict:
    if not provider.startswith("custom:"):
        raise ValueError(f"未知提供商: {provider}")
    service_id = provider.split(":", 1)[1]
    service = get_service(service_id)
    if not service:
        raise ValueError(f"自定义服务不存在: {service_id}")
    return {
        "label": service["name"],
        "default_model": service["default_model"],
        "models": list(service["models"]),
        "context_window": 128000,
        "supports_tools": True,
        "base_url": service["base_url"],
        "protocol": service["protocol"],
        "service_id": service_id,
        "custom": True,
    }


def _validated(payload: dict, existing: dict | None = None) -> dict:
    name = str(payload.get("name", existing.get("name", "") if existing else "")).strip()
    protocol = str(payload.get("protocol", existing.get("protocol", "openai") if existing else "openai")).strip().lower()
    base_url = str(payload.get("base_url", existing.get("base_url", "") if existing else "")).strip().rstrip("/")
    raw_models = payload.get("models", existing.get("models", []) if existing else [])
    if isinstance(raw_models, str):
        raw_models = re.split(r"[,\n;]+", raw_models)
    if not isinstance(raw_models, list):
        raise ValueError("模型列表格式无效")
    models = list(dict.fromkeys(str(model).strip() for model in raw_models if str(model).strip()))
    default_model = str(payload.get("default_model", existing.get("default_model", "") if existing else "")).strip()
    if not name:
        raise ValueError("服务名称不能为空")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError("协议仅支持 OpenAI、Anthropic Messages 或 Gemini")
    if not base_url:
        raise ValueError("Base URL 不能为空")
    if not models:
        raise ValueError("请至少添加一个模型")
    if len(models) > 100:
        raise ValueError("每套服务最多可添加 100 个模型")
    if default_model not in models:
        default_model = models[0]
    return {
        "name": name[:80],
        "protocol": protocol,
        "base_url": base_url,
        "models": models,
        "default_model": default_model,
    }


def create_service(payload: dict) -> dict:
    service = _validated(payload)
    service_id = secrets.token_hex(6)
    service["id"] = service_id
    with _lock:
        services = _read_services()
        services.append(service)
        _write_services(services)
    _store_key(service_id, payload.get("api_key"))
    return _public_service(service)


def update_service(service_id: str, payload: dict) -> dict:
    with _lock:
        services = _read_services()
        index = next((i for i, item in enumerate(services) if item["id"] == service_id), None)
        if index is None:
            raise KeyError(service_id)
        updated = _validated(payload, services[index])
        updated["id"] = service_id
        services[index] = updated
        _write_services(services)
    if payload.get("api_key"):
        _store_key(service_id, payload["api_key"])
    return _public_service(updated)


def _store_key(service_id: str, value) -> None:
    key = str(value or "").strip()
    if not key:
        return
    if service_id == "legacy":
        update_env({"CUSTOM_API_KEY": key}, ENV_PATH)
        settings.CUSTOM_API_KEY = key
        return
    env_key = _api_key_env(service_id)
    update_env({env_key: key}, ENV_PATH)
    os.environ[env_key] = key


def delete_service(service_id: str) -> bool:
    with _lock:
        services = _read_services()
        remaining = [item for item in services if item["id"] != service_id]
        if len(remaining) == len(services):
            return False
        if service_id == "legacy":
            updates = {"CUSTOM_API_BASE_URL": "", "CUSTOM_API_KEY": "", "CUSTOM_API_MODEL": ""}
            update_env(updates, ENV_PATH)
            for name, value in updates.items():
                setattr(settings, name, value)
        _write_services(remaining)
    if service_id == "legacy":
        return True
    env_key = _api_key_env(service_id)
    update_env({env_key: ""}, ENV_PATH)
    os.environ.pop(env_key, None)
    return True


def migrate_legacy_custom_api() -> dict | None:
    """把旧版单一自定义 API 配置迁移为可管理的 legacy 服务。"""
    with _lock:
        services = _read_services()
        existing = next((item for item in services if item["id"] == "legacy"), None)
        if existing:
            from utils.prefs import load_prefs, save_prefs

            prefs = load_prefs()
            if prefs.get("provider") == "custom":
                old_model = str(
                    prefs.get("custom_models", {}).get("custom", "")
                    or existing.get("default_model", "")
                )
                save_prefs(f"custom:{existing['id']}", old_model, prefs.get("custom_models", {}))
            return dict(existing)
        base_url = str(settings.CUSTOM_API_BASE_URL or "").strip()
        if not base_url:
            return None
        from utils.prefs import load_prefs, save_prefs

        prefs = load_prefs()
        old_model = str(settings.CUSTOM_API_MODEL or prefs.get("custom_models", {}).get("custom", "")).strip()
        migrated = {
            "id": "legacy",
            "name": "旧版自定义 API",
            "protocol": "openai",
            "base_url": base_url,
            "models": [old_model] if old_model else [],
            "default_model": old_model,
        }
        services.append(migrated)
        _write_services(services)
        if prefs.get("provider") == "custom":
            save_prefs("custom:legacy", old_model, prefs.get("custom_models", {}))
        return dict(migrated)
