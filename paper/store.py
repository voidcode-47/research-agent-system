"""论文数据存储：index.json + data/papers/{paper_id}/ 目录。

paper_id 严格为 p_ + 12 位 hex；任何路径拼接前都会校验 ID 与最终路径
是否仍位于论文根目录内，防止目录穿越。
"""
import datetime
import json
import os
import re
import secrets
import shutil

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(BASE_DIR, "data", "papers")
_ID_RE = re.compile(r"^p_[0-9a-f]{12}$")
_ANALYSIS_DIMS = ("level", "feasibility", "format", "deai")


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def root_dir() -> str:
    """论文数据根目录（支持运行时被设置/测试改写）。"""
    raw = settings.PAPER_DATA_DIR or DEFAULT_ROOT
    path = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
    return os.path.abspath(path)


def ensure_root() -> str:
    root = root_dir()
    os.makedirs(root, exist_ok=True)
    return root


def new_paper_id() -> str:
    return "p_" + secrets.token_hex(6)


def validate_paper_id(paper_id: str) -> str:
    pid = (paper_id or "").strip()
    if not _ID_RE.match(pid):
        raise ValueError("非法的论文 ID")
    return pid


def paper_dir(paper_id: str) -> str:
    pid = validate_paper_id(paper_id)
    root = os.path.abspath(root_dir())
    path = os.path.abspath(os.path.join(root, pid))
    if not (path == root or path.startswith(root + os.sep)):
        raise ValueError("非法的论文路径")
    return path


def create_paper_dir(paper_id: str) -> str:
    path = paper_dir(paper_id)
    for sub in ("versions", "analysis", "exports"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# meta / parsed / requirement / analysis / similar
# ---------------------------------------------------------------------------
def save_meta(paper_id: str, meta: dict) -> None:
    pid = validate_paper_id(paper_id)
    create_paper_dir(pid)
    meta = dict(meta or {})
    meta["id"] = pid
    meta["updated_at"] = _now()
    meta.setdefault("created_at", meta["updated_at"])
    _write_json(os.path.join(paper_dir(pid), "meta.json"), meta)
    _upsert_index(meta)


def load_meta(paper_id: str) -> dict:
    path = os.path.join(paper_dir(paper_id), "meta.json")
    meta = _read_json(path)
    if not isinstance(meta, dict):
        raise FileNotFoundError("论文不存在")
    return meta


def save_parsed(paper_id: str, parsed: dict) -> None:
    _write_json(os.path.join(paper_dir(paper_id), "parsed.json"), parsed)


def load_parsed(paper_id: str) -> dict:
    data = _read_json(os.path.join(paper_dir(paper_id), "parsed.json"))
    if not isinstance(data, dict):
        raise FileNotFoundError("论文解析结果不存在")
    return data


def save_requirement(paper_id: str, requirement: dict) -> None:
    pid = validate_paper_id(paper_id)
    _write_json(os.path.join(paper_dir(pid), "requirement.json"), requirement)
    try:
        meta = load_meta(pid)
        meta["status"] = "confirmed" if requirement.get("confirmed") else "uploaded"
        save_meta(pid, meta)
    except FileNotFoundError:
        pass
    mark_analysis_stale(pid)


def load_requirement(paper_id: str) -> dict | None:
    data = _read_json(os.path.join(paper_dir(paper_id), "requirement.json"))
    return data if isinstance(data, dict) else None


def save_analysis(paper_id: str, dimension: str, data: dict) -> None:
    if dimension not in _ANALYSIS_DIMS:
        raise ValueError(f"未知诊断维度：{dimension}")
    path = os.path.join(paper_dir(paper_id), "analysis", f"{dimension}.json")
    _write_json(path, data)


def load_analysis(paper_id: str, dimension: str) -> dict | None:
    if dimension not in _ANALYSIS_DIMS:
        return None
    data = _read_json(os.path.join(paper_dir(paper_id), "analysis", f"{dimension}.json"))
    return data if isinstance(data, dict) else None


def has_analysis(paper_id: str, dimension: str) -> bool:
    data = load_analysis(paper_id, dimension)
    return bool(data) and not data.get("stale")


def mark_analysis_stale(paper_id: str) -> int:
    """需求变更后把已有诊断标记为 stale，并把 meta 状态退回 confirmed。"""
    pid = validate_paper_id(paper_id)
    try:
        base = os.path.join(paper_dir(pid), "analysis")
    except ValueError:
        return 0
    count = 0
    if os.path.isdir(base):
        for name in os.listdir(base):
            if not name.endswith(".json"):
                continue
            path = os.path.join(base, name)
            data = _read_json(path)
            if isinstance(data, dict) and not data.get("stale"):
                data["stale"] = True
                _write_json(path, data)
                count += 1
    if count:
        try:
            meta = load_meta(pid)
            if meta.get("status") == "analyzed":
                meta["status"] = "confirmed"
                save_meta(pid, meta)
        except FileNotFoundError:
            pass
    return count


def save_similar(paper_id: str, data: dict) -> None:
    _write_json(os.path.join(paper_dir(paper_id), "similar.json"), data)


def load_similar(paper_id: str) -> dict | None:
    data = _read_json(os.path.join(paper_dir(paper_id), "similar.json"))
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------
def _version_path(paper_id: str, n: int) -> str:
    return os.path.join(paper_dir(paper_id), "versions", f"v{int(n)}.json")


def save_version(paper_id: str, paragraphs: list, note: str = "") -> int:
    pid = validate_paper_id(paper_id)
    create_paper_dir(pid)
    existing = [v["version"] for v in list_versions(pid)]
    n = (max(existing) + 1) if existing else 0
    _write_json(_version_path(pid, n), {
        "version": n,
        "paragraphs": paragraphs,
        "note": note,
        "created_at": _now(),
    })
    try:
        meta = load_meta(pid)
        meta["current_version"] = n
        meta["paragraph_count"] = len(paragraphs)
        save_meta(pid, meta)
    except FileNotFoundError:
        pass
    return n


def load_version(paper_id: str, n: int) -> dict:
    data = _read_json(_version_path(paper_id, n))
    if not isinstance(data, dict):
        raise FileNotFoundError(f"版本 v{n} 不存在")
    return data


def list_versions(paper_id: str) -> list[dict]:
    base = os.path.join(paper_dir(paper_id), "versions")
    out = []
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        m = re.match(r"^v(\d+)\.json$", name)
        if not m:
            continue
        n = int(m.group(1))
        data = _read_json(os.path.join(base, name)) or {}
        out.append({
            "version": n,
            "created_at": data.get("created_at", ""),
            "note": data.get("note", ""),
        })
    return sorted(out, key=lambda x: x["version"])


def current_paragraphs(paper_id: str) -> list[dict]:
    meta = load_meta(paper_id)
    n = int(meta.get("current_version") or 0)
    try:
        return load_version(paper_id, n).get("paragraphs") or []
    except FileNotFoundError:
        return load_parsed(paper_id).get("paragraphs") or []


# ---------------------------------------------------------------------------
# 列表 / 删除
# ---------------------------------------------------------------------------
def _index_path() -> str:
    return os.path.join(ensure_root(), "index.json")


def _load_index() -> list[dict]:
    data = _read_json(_index_path(), [])
    return data if isinstance(data, list) else []


def _upsert_index(meta: dict) -> None:
    items = [m for m in _load_index() if m.get("id") != meta.get("id")]
    items.append({
        "id": meta.get("id"),
        "filename": meta.get("filename", ""),
        "title": meta.get("title", ""),
        "pages": meta.get("pages", 0),
        "chars": meta.get("chars", 0),
        "paragraph_count": meta.get("paragraph_count", 0),
        "status": meta.get("status", "uploaded"),
        "current_version": meta.get("current_version", 0),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
    })
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    _write_json(_index_path(), items)


def list_papers() -> list[dict]:
    root = ensure_root()
    out = []
    for item in _load_index():
        pid = item.get("id", "")
        if not _ID_RE.match(pid or ""):
            continue
        if not os.path.isdir(os.path.join(root, pid)):
            continue
        item = dict(item)
        item["analyzed"] = {
            d: has_analysis(pid, d) for d in _ANALYSIS_DIMS
        }
        out.append(item)
    return out


def delete_paper(paper_id: str) -> bool:
    pid = validate_paper_id(paper_id)
    path = paper_dir(pid)
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path)
    items = [m for m in _load_index() if m.get("id") != pid]
    _write_json(_index_path(), items)
    return True
