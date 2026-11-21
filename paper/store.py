"""论文数据存储：index.json + data/papers/{paper_id}/ 目录。

paper_id 严格为 p_ + 12 位 hex；任何路径拼接前都会校验 ID 与最终路径
是否仍位于论文根目录内，防止目录穿越。

并发保证（仅单进程）：所有“读-改-写”操作（meta + index、版本号分配、
采纳改写、级联标记过期）都在同一把可重入进程锁内完成；JSON 先写入
唯一临时文件再 os.replace，避免并发写共享临时文件。该锁不提供跨进程
互斥；本应用按单服务进程运行，如改为多进程需改用数据库或文件锁。
"""
import datetime
import gzip
import json
import os
import re
import secrets
import shutil
import threading
import uuid
import zlib

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(BASE_DIR, "data", "papers")
_ID_RE = re.compile(r"^p_[0-9a-f]{12}$")
_ANALYSIS_DIMS = ("level", "feasibility", "format", "deai")
_ARCHIVE_NAME = "archive.json.gz"
_ARCHIVE_FORMAT = 1
_HISTORY_NAME = "history.json"
_HISTORY_LIMIT = 200

_LOCK = threading.RLock()


class VersionConflict(Exception):
    """改写结果与当前论文正文不一致，拒绝采纳。"""

    def __init__(self, message: str, *, current_version: int,
                 expected_version: int | None, reason: str):
        super().__init__(message)
        self.current_version = current_version
        self.expected_version = expected_version
        self.reason = reason


def locked():
    """返回存储层进程锁，供跨多次读写的复合操作使用。"""
    return _LOCK


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
    """原子写入：唯一临时文件 + fsync + os.replace。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _write_bytes(path: str, data: bytes) -> None:
    """原子写入二进制文件（版本归档使用）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_file(path: str, data) -> None:
    """原子写入运行时 JSON 文件（如任务日志），不进入论文索引。"""
    _write_json(os.path.abspath(path), data)


def read_json_file(path: str, default=None):
    return _read_json(os.path.abspath(path), default)


# ---------------------------------------------------------------------------
# meta / parsed / requirement / analysis / similar
# ---------------------------------------------------------------------------
def save_meta(paper_id: str, meta: dict) -> None:
    pid = validate_paper_id(paper_id)
    with _LOCK:
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
    with _LOCK:
        load_meta(paper_id)
        _write_json(os.path.join(paper_dir(paper_id), "parsed.json"), parsed)


def load_parsed(paper_id: str) -> dict:
    data = _read_json(os.path.join(paper_dir(paper_id), "parsed.json"))
    if not isinstance(data, dict):
        raise FileNotFoundError("论文解析结果不存在")
    return data


def save_requirement(paper_id: str, requirement: dict) -> None:
    pid = validate_paper_id(paper_id)
    with _LOCK:
        meta = load_meta(pid)
        _write_json(os.path.join(paper_dir(pid), "requirement.json"), requirement)
        meta["status"] = "confirmed" if requirement.get("confirmed") else "uploaded"
        save_meta(pid, meta)
        mark_analysis_stale(pid)


def load_requirement(paper_id: str) -> dict | None:
    data = _read_json(os.path.join(paper_dir(paper_id), "requirement.json"))
    return data if isinstance(data, dict) else None


def save_analysis(paper_id: str, dimension: str, data: dict) -> None:
    if dimension not in _ANALYSIS_DIMS:
        raise ValueError(f"未知诊断维度：{dimension}")
    path = os.path.join(paper_dir(paper_id), "analysis", f"{dimension}.json")
    with _LOCK:
        load_meta(paper_id)
        _write_json(path, data)


def analysis_snapshot_matches(paper_id: str, version: int,
                              requirement: dict) -> bool:
    """确认诊断使用的正文版本和需求仍是当前值。"""
    pid = validate_paper_id(paper_id)
    with _LOCK:
        try:
            meta = load_meta(pid)
        except FileNotFoundError:
            return False
        return (int(meta.get("current_version") or 0) == int(version)
                and load_requirement(pid) == requirement)


def save_analysis_if_current(paper_id: str, dimension: str, data: dict,
                             version: int, requirement: dict) -> bool:
    """仅在诊断快照仍有效时原子保存结果。"""
    if dimension not in _ANALYSIS_DIMS:
        raise ValueError(f"未知诊断维度：{dimension}")
    pid = validate_paper_id(paper_id)
    with _LOCK:
        if not analysis_snapshot_matches(pid, version, requirement):
            return False
        path = os.path.join(paper_dir(pid), "analysis", f"{dimension}.json")
        _write_json(path, data)
        append_analysis_history(pid, _history_entry(dimension, version, data))
        return True


def _history_path(paper_id: str) -> str:
    return os.path.join(paper_dir(paper_id), "analysis", _HISTORY_NAME)


def _history_key(item: dict) -> tuple:
    return (item.get("dimension"), item.get("version"),
            item.get("generated_at"), item.get("score"))


def _history_entry(dimension: str, version: int, data: dict) -> dict:
    """把维度结果压缩成趋势所需的最小字段，非 deai 不写 AI 相关键。"""
    entry = {
        "dimension": dimension,
        "version": int(version or 0),
        "generated_at": str(data.get("generated_at") or ""),
        "score": data.get("score"),
    }
    for key in ("ai_rate", "ai_score", "paragraph_risk"):
        if data.get(key) is not None:
            entry[key] = data[key]
    return entry


def append_analysis_history(paper_id: str, record: dict) -> None:
    """追加一条诊断历史；重复记录跳过，只保留最近 _HISTORY_LIMIT 条。"""
    if not isinstance(record, dict) or not record.get("dimension"):
        return
    pid = validate_paper_id(paper_id)
    with _LOCK:
        path = _history_path(pid)
        items = _read_json(path, [])
        if not isinstance(items, list):
            items = []
        entry = {
            "dimension": str(record.get("dimension")),
            "version": int(record.get("version") or 0),
            "generated_at": str(record.get("generated_at") or ""),
            "score": record.get("score"),
        }
        for key in ("ai_rate", "ai_score", "paragraph_risk"):
            if record.get(key) is not None:
                entry[key] = record[key]
        key = _history_key(entry)
        if any(isinstance(it, dict) and _history_key(it) == key for it in items):
            return
        items.append(entry)
        _write_json(path, items[-_HISTORY_LIMIT:])


def load_analysis_history(paper_id: str, limit: int = 40) -> list[dict]:
    """读取最近 limit 条诊断历史；缺失或损坏时返回空列表。"""
    try:
        items = _read_json(_history_path(paper_id), [])
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    rows = [it for it in items if isinstance(it, dict)]
    if limit and limit > 0:
        rows = rows[-limit:]
    return rows


def load_analysis(paper_id: str, dimension: str) -> dict | None:
    if dimension not in _ANALYSIS_DIMS:
        return None
    data = _read_json(os.path.join(paper_dir(paper_id), "analysis", f"{dimension}.json"))
    if not isinstance(data, dict):
        return None
    # 旧版 deai 结果缺少 AI 生成率且分数方向相反（越高越像 AI），视为过期，需重新诊断
    if dimension == "deai" and "ai_rate" not in data and not data.get("stale"):
        data = dict(data)
        data["stale"] = True
    return data


def has_analysis(paper_id: str, dimension: str) -> bool:
    data = load_analysis(paper_id, dimension)
    return bool(data) and not data.get("stale")


def mark_analysis_stale(paper_id: str) -> int:
    """需求变更后把已有诊断标记为 stale，并把 meta 状态退回 confirmed。"""
    pid = validate_paper_id(paper_id)
    with _LOCK:
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
    pid = validate_paper_id(paper_id)
    with _LOCK:
        load_meta(pid)
        _write_json(os.path.join(paper_dir(pid), "similar.json"), data)


def load_similar(paper_id: str) -> dict | None:
    data = _read_json(os.path.join(paper_dir(paper_id), "similar.json"))
    return data if isinstance(data, dict) else None


def save_rewrite_guide(paper_id: str, data: dict) -> None:
    with _LOCK:
        load_meta(paper_id)
        _write_json(os.path.join(paper_dir(paper_id), "rewrite_guide.json"), data)


def load_rewrite_guide(paper_id: str) -> dict | None:
    data = _read_json(os.path.join(paper_dir(paper_id), "rewrite_guide.json"))
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------
def _version_path(paper_id: str, n: int) -> str:
    return os.path.join(paper_dir(paper_id), "versions", f"v{int(n)}.json")


def _archive_path(paper_id: str) -> str:
    return os.path.join(paper_dir(paper_id), "versions", _ARCHIVE_NAME)


def _loose_version_numbers(paper_id: str) -> list[int]:
    base = os.path.join(paper_dir(paper_id), "versions")
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        m = re.match(r"^v(\d+)\.json$", name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def _load_version_archive(paper_id: str) -> dict[int, dict]:
    """读取压缩归档；不存在返回空，损坏时明确报错而不是静默丢失。"""
    path = _archive_path(paper_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as f:
            raw = gzip.decompress(f.read())
        data = json.loads(raw.decode("utf-8"))
    except (OSError, EOFError, ValueError, zlib.error) as e:
        raise ValueError("版本归档文件损坏，无法读取") from e
    if not isinstance(data, dict) or data.get("format") != _ARCHIVE_FORMAT \
            or not isinstance(data.get("versions"), dict):
        raise ValueError("版本归档文件格式不支持")
    out: dict[int, dict] = {}
    for key, value in data["versions"].items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            raise ValueError("版本归档包含非法版本号")
        if n < 0 or not isinstance(value, dict) \
                or not isinstance(value.get("paragraphs"), list):
            raise ValueError(f"版本归档中的 v{n} 已损坏")
        out[n] = value
    return out


def _merge_version_entries(paper_id: str) -> list[dict]:
    """合并未归档与已归档版本；未归档文件优先。"""
    merged: dict[int, dict] = {}
    for n in _loose_version_numbers(paper_id):
        data = _read_json(_version_path(paper_id, n)) or {}
        merged[n] = {
            "version": n,
            "created_at": data.get("created_at", ""),
            "note": data.get("note", ""),
            "archived": False,
        }
    for n, data in _load_version_archive(paper_id).items():
        if n in merged:
            continue
        merged[n] = {
            "version": n,
            "created_at": data.get("created_at", ""),
            "note": data.get("note", ""),
            "archived": True,
        }
    return sorted(merged.values(), key=lambda x: x["version"])


def save_version(paper_id: str, paragraphs: list, note: str = "") -> int:
    pid = validate_paper_id(paper_id)
    with _LOCK:
        load_meta(pid)
        create_paper_dir(pid)
        existing = set(_loose_version_numbers(pid))
        existing.update(_load_version_archive(pid))
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
    with _LOCK:
        data = _read_json(_version_path(paper_id, n))
        if isinstance(data, dict):
            return data
        archived = _load_version_archive(paper_id)
        if int(n) in archived:
            return archived[int(n)]
    raise FileNotFoundError(f"版本 v{n} 不存在")


def list_versions(paper_id: str) -> list[dict]:
    with _LOCK:
        return _merge_version_entries(paper_id)


def compact_versions(paper_id: str, keep_recent: int = 5) -> dict:
    """把较早的未归档版本压缩进单个归档文件，保留 v0、当前版本和最近版本。

    归档成功后才删除原始版本文件；归档中的版本仍可通过 list/load 读取。
    """
    pid = validate_paper_id(paper_id)
    keep_recent = int(keep_recent)
    if keep_recent < 0:
        raise ValueError("保留版本数不能为负数")
    with _LOCK:
        meta = load_meta(pid)
        current = int(meta.get("current_version") or 0)
        loose = _loose_version_numbers(pid)
        archived = _load_version_archive(pid)
        keep = {0, current}
        if keep_recent > 0:
            keep.update(loose[-keep_recent:])
        candidates = [n for n in loose if n not in keep]
        if not candidates:
            return {
                "archived_versions": [],
                "removed_versions": [],
                "kept_versions": sorted(keep),
                "current_version": current,
                "loose_count": len(loose),
                "archived_count": len(archived),
                "version_count": len(set(loose) | set(archived)),
            }

        loaded: dict[int, dict] = {}
        for n in candidates:
            data = _read_json(_version_path(pid, n))
            if not isinstance(data, dict) or not isinstance(data.get("paragraphs"), list):
                raise ValueError(f"版本 v{n} 已损坏，未执行整理")
            loaded[n] = data

        merged = dict(archived)
        merged.update(loaded)
        payload = {
            "format": _ARCHIVE_FORMAT,
            "archived_at": _now(),
            "versions": {str(n): merged[n] for n in sorted(merged)},
        }
        compressed = gzip.compress(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            mtime=0,
        )
        _write_bytes(_archive_path(pid), compressed)

        removed = []
        for n in candidates:
            try:
                os.remove(_version_path(pid, n))
                removed.append(n)
            except OSError as e:
                logger.warning(f"版本 v{n} 归档后删除失败: {e}")
        after_loose = _loose_version_numbers(pid)
        after_archive = _load_version_archive(pid)
        return {
            "archived_versions": candidates,
            "removed_versions": removed,
            "kept_versions": sorted(keep),
            "current_version": current,
            "loose_count": len(after_loose),
            "archived_count": len(after_archive),
            "version_count": len(set(after_loose) | set(after_archive)),
        }


def current_version(paper_id: str) -> int:
    with _LOCK:
        return int(load_meta(paper_id).get("current_version") or 0)


def snapshot_current(paper_id: str) -> tuple[int, list[dict]]:
    """在同一把锁内读取当前版本号与正文，供改写任务使用一致快照。"""
    with _LOCK:
        meta = load_meta(paper_id)
        n = int(meta.get("current_version") or 0)
        try:
            paragraphs = load_version(paper_id, n).get("paragraphs") or []
        except FileNotFoundError:
            paragraphs = load_parsed(paper_id).get("paragraphs") or []
        return n, paragraphs


def current_paragraphs(paper_id: str) -> list[dict]:
    return snapshot_current(paper_id)[1]


_CURRENT_CACHE: dict[tuple, dict] = {}
_CURRENT_CACHE_MAX = 16


def _file_stamp(path: str):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _remember_current(key: tuple, value: dict) -> None:
    if len(_CURRENT_CACHE) >= _CURRENT_CACHE_MAX:
        _CURRENT_CACHE.pop(next(iter(_CURRENT_CACHE)), None)
    _CURRENT_CACHE[key] = value


def current_parsed(paper_id: str) -> dict:
    """读取当前版本的正文和摘要，保留上传时的 parsed.json 与原始版本。

    结果按 (数据目录, 版本号, 文件时间戳) 缓存在进程内；返回值是只读视图，
    调用方不得原地修改。
    """
    from paper.parser import _extract_abstract, _extract_keywords

    with _LOCK:
        pdir = paper_dir(paper_id)
        meta = load_meta(paper_id)
        n = int(meta.get("current_version") or 0)
        if not n:
            return load_parsed(paper_id)
        key = (
            pdir,
            n,
            _file_stamp(os.path.join(pdir, "parsed.json")),
            _file_stamp(_version_path(paper_id, n)),
            _file_stamp(_archive_path(paper_id)),
        )
        cached = _CURRENT_CACHE.get(key)
        if cached is not None:
            return cached
        parsed = load_parsed(paper_id)
        try:
            paragraphs = load_version(paper_id, n).get("paragraphs") or []
        except FileNotFoundError:
            paragraphs = parsed.get("paragraphs") or []
        full_text = "\n".join(p.get("text") or "" for p in paragraphs)
        result = {
            **parsed,
            "paragraphs": paragraphs,
            "text_chars": len(re.sub(r"\s", "", full_text)),
            "abstract": _extract_abstract(parsed.get("sections") or [], paragraphs, full_text),
            "keywords": _extract_keywords(paragraphs),
        }
        _remember_current(key, result)
        return result


def current_snapshot(paper_id: str) -> tuple[int, dict]:
    """在同一把锁内返回当前版本号与正文视图，避免版本号/正文错配。"""
    with _LOCK:
        n = int(load_meta(paper_id).get("current_version") or 0)
        return n, current_parsed(paper_id)


# ---------------------------------------------------------------------------
# 列表 / 删除
# ---------------------------------------------------------------------------
def _index_path() -> str:
    return os.path.join(ensure_root(), "index.json")


def _index_entry(meta: dict) -> dict:
    return {
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
    }


def _scan_papers() -> list[dict]:
    """从论文目录的 meta.json 重建索引条目（索引丢失/损坏时使用）。"""
    root = ensure_root()
    items = []
    for name in os.listdir(root):
        if not _ID_RE.match(name) or not os.path.isdir(os.path.join(root, name)):
            continue
        meta = _read_json(os.path.join(root, name, "meta.json"))
        if isinstance(meta, dict):
            items.append(_index_entry({**meta, "id": name}))
    return items


def _load_index() -> list[dict]:
    path = _index_path()
    if not os.path.exists(path):
        return _scan_papers()
    data = _read_json(path)
    return data if isinstance(data, list) else _scan_papers()


def rebuild_index() -> list[dict]:
    """按论文目录重建 index.json，返回重建后的条目。"""
    with _LOCK:
        items = sorted(
            _scan_papers(), key=lambda x: x.get("updated_at", ""), reverse=True
        )
        _write_json(_index_path(), items)
        return items


def _upsert_index(meta: dict) -> None:
    with _LOCK:
        items = [m for m in _load_index() if isinstance(m, dict)
                 and m.get("id") != meta.get("id")]
        items.append(_index_entry(meta))
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        _write_json(_index_path(), items)


def _paper_dirs() -> set[str]:
    root = ensure_root()
    return {
        name for name in os.listdir(root)
        if _ID_RE.match(name) and os.path.isdir(os.path.join(root, name))
    }


def _index_entries() -> list[dict]:
    """读取并校验索引；缺失或与论文目录不一致时按目录重建。

    正常路径只读取 index.json，不扫描各论文的 meta.json。索引仍是一个
    JSON 文件，保存时整文件重写；这里保证的是列表/搜索不会触发全量扫描。
    """
    with _LOCK:
        dirs = _paper_dirs()
        path = _index_path()
        if not os.path.exists(path) or not isinstance(_read_json(path), list):
            items = rebuild_index()
        else:
            items = _load_index()
            if {m.get("id") for m in items if isinstance(m, dict)} != dirs:
                items = rebuild_index()
        return [
            dict(item) for item in items
            if isinstance(item, dict) and item.get("id") in dirs
        ]


def _decorate_index_item(item: dict) -> dict:
    item = dict(item)
    pid = item.get("id", "")
    item["analyzed"] = {d: has_analysis(pid, d) for d in _ANALYSIS_DIMS}
    return item


def list_papers() -> list[dict]:
    return [_decorate_index_item(item) for item in _index_entries()]


def search_papers(query: str = "", offset: int = 0, limit: int = 50) -> dict:
    """按标题/文件名/ID 搜索并分页；只装饰当前页，避免计算全部论文状态。"""
    query = (query or "").strip()
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    needle = query.casefold()
    items = _index_entries()
    if needle:
        items = [
            item for item in items
            if needle in " ".join(str(item.get(key) or "") for key in
                                  ("title", "filename", "id")).casefold()
        ]
    total = len(items)
    page = items[offset:offset + limit]
    return {
        "papers": [_decorate_index_item(item) for item in page],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


def delete_paper(paper_id: str) -> bool:
    pid = validate_paper_id(paper_id)
    with _LOCK:
        path = paper_dir(pid)
        existed = os.path.isdir(path)
        if existed:
            shutil.rmtree(path)
        for cache_key in [key for key in _CURRENT_CACHE if key[0] == path]:
            _CURRENT_CACHE.pop(cache_key, None)
        items = [m for m in _load_index() if m.get("id") != pid]
        _write_json(_index_path(), items)
        return existed
