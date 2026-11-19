"""论文备份与恢复：带清单的 zip 归档 + 严格校验。

备份只包含单篇论文目录中的已知持久化文件，不包含导出文件。恢复总是创建
一篇新论文，不覆盖现有论文；归档中的路径、哈希、大小和 JSON 结构都会在
落盘前校验，避免路径穿越、伪造清单和损坏数据进入存储层。
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
import zlib

from paper import store
from utils.logger import get_logger

logger = get_logger(__name__)

BACKUP_APP = "paper-workbench"
BACKUP_FORMAT = 1
MAX_BACKUP_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_BACKUP_FILES = 2000
MAX_BACKUP_FILE_BYTES = 200 * 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_JSON_BYTES = 200 * 1024 * 1024
MAX_ARCHIVED_VERSIONS = 10000

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_RE = re.compile(
    r"^paper/(?:"
    r"meta\.json|parsed\.json|requirement\.json|similar\.json|rewrite_guide\.json|"
    r"analysis/(?:level|feasibility|format|deai)\.json|"
    r"versions/(?:v\d+\.json|archive\.json\.gz)|"
    r"original\.(?:pdf|docx)"
    r")$"
)
_VERSION_RE = re.compile(r"^paper/versions/v(\d+)\.json$")


class BackupError(Exception):
    """备份或恢复校验失败。"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _sha256_file(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _backup_files(paper_id: str) -> list[tuple[str, str]]:
    base = store.paper_dir(paper_id)
    out = []
    for root, dirs, files in os.walk(base):
        rel_root = os.path.relpath(root, base)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in ("exports",)]
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".tmp"):
                continue
            full = os.path.join(root, name)
            if os.path.islink(full):
                raise BackupError("论文目录包含符号链接，拒绝备份")
            rel = os.path.join(rel_root, name).replace(os.sep, "/")
            if rel.startswith("./"):
                rel = rel[2:]
            rel = f"paper/{rel}" if not rel.startswith("paper/") else rel
            if _ALLOWED_RE.match(rel):
                out.append((rel, full))
    return sorted(out)


def create_backup_archive(paper_id: str, dest_path: str) -> dict:
    """生成 zip 备份；先写临时文件，成功后原子替换目标文件。"""
    pid = store.validate_paper_id(paper_id)
    store.load_meta(pid)
    files = _backup_files(pid)
    paths = {rel for rel, _ in files}
    required = {"paper/meta.json", "paper/parsed.json"}
    if not required.issubset(paths):
        raise BackupError("论文数据不完整，缺少元数据或解析结果，无法备份")
    if not ({"paper/original.pdf", "paper/original.docx"} & paths):
        raise BackupError("论文原文缺失，无法备份")

    tmp = f"{dest_path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    records = []
    total = 0
    try:
        with zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            for rel, full in files:
                digest = hashlib.sha256()
                size = 0
                info = zipfile.ZipInfo(rel, date_time=time.localtime()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as src, zf.open(info, "w") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_BACKUP_FILE_BYTES \
                                or total > MAX_BACKUP_UNCOMPRESSED_BYTES:
                            raise BackupError("论文数据超过备份体积上限")
                        digest.update(chunk)
                        dst.write(chunk)
                records.append({
                    "path": rel,
                    "size": size,
                    "sha256": digest.hexdigest(),
                })
            manifest = {
                "app": BACKUP_APP,
                "format": BACKUP_FORMAT,
                "paper_id": pid,
                "created_at": _now(),
                "file_count": len(records),
                "files": records,
            }
            zf.writestr("manifest.json", _json_bytes(manifest))
        os.replace(tmp, dest_path)
    except BackupError:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise BackupError(f"生成备份失败：{e}") from e
    return {
        "paper_id": pid,
        "created_at": manifest["created_at"],
        "file_count": len(records),
        "size": os.path.getsize(dest_path),
    }


def _find_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        info = zf.getinfo("manifest.json")
    except KeyError:
        raise BackupError("备份缺少 manifest.json")
    if info.file_size > MAX_MANIFEST_BYTES:
        raise BackupError("备份清单过大")
    if info.compress_size > MAX_MANIFEST_BYTES:
        raise BackupError("备份清单压缩后异常过大")
    try:
        with zf.open(info) as f:
            raw = f.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, zipfile.BadZipFile, zlib.error) as e:
        raise BackupError("无法读取备份清单") from e
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BackupError("备份清单解压后过大")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BackupError("备份清单不是有效 JSON") from e
    if not isinstance(data, dict) or data.get("app") != BACKUP_APP \
            or data.get("format") != BACKUP_FORMAT:
        raise BackupError("不是本应用支持的备份格式")
    try:
        store.validate_paper_id(str(data.get("paper_id") or ""))
    except ValueError:
        raise BackupError("备份清单中的论文 ID 非法")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise BackupError("备份清单缺少文件列表")
    if len(files) > MAX_BACKUP_FILES:
        raise BackupError("备份文件数量超过上限")
    seen = set()
    total = 0
    records = []
    for item in files:
        if not isinstance(item, dict):
            raise BackupError("备份清单包含非法文件项")
        path = item.get("path")
        size = item.get("size")
        digest = item.get("sha256")
        if not isinstance(path, str) or not _ALLOWED_RE.match(path):
            raise BackupError(f"备份包含不允许的路径：{path}")
        if path in seen or path.lower() in {p.lower() for p in seen}:
            raise BackupError(f"备份包含重复路径：{path}")
        seen.add(path)
        if not isinstance(size, int) or size < 0 or size > MAX_BACKUP_FILE_BYTES:
            raise BackupError(f"备份文件大小非法：{path}")
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise BackupError(f"备份文件哈希非法：{path}")
        total += size
        if total > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise BackupError("备份解压后体积超过上限")
        records.append({"path": path, "size": size, "sha256": digest})
    return {**data, "files": records}


def _validate_zip_entries(zf: zipfile.ZipFile, records: list[dict]) -> dict:
    expected = {rec["path"] for rec in records}
    actual = set()
    seen_lower = set()
    manifest_count = 0
    for info in zf.infolist():
        name = info.filename
        if name == "manifest.json":
            manifest_count += 1
            continue
        if info.is_dir():
            raise BackupError(f"备份包含目录项：{name}")
        if name.endswith("/"):
            raise BackupError(f"备份包含目录项：{name}")
        if name not in expected:
            raise BackupError(f"备份包含清单外文件：{name}")
        if name.lower() in seen_lower:
            raise BackupError(f"备份包含重复路径：{name}")
        seen_lower.add(name.lower())
        if info.flag_bits & 0x1:
            raise BackupError("不支持加密的备份条目")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise BackupError("不支持符号链接备份条目")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BackupError(f"不支持的压缩方式：{name}")
        actual.add(name)
    if manifest_count != 1:
        raise BackupError("备份清单条目数量异常")
    if actual != expected:
        missing = sorted(expected - actual)
        raise BackupError(f"备份缺少文件：{missing[0]}")
    return {info.filename: info for info in zf.infolist()
            if info.filename != "manifest.json"}


def _extract_files(zf: zipfile.ZipFile, records: list[dict], infos: dict,
                   staging: str) -> None:
    total = 0
    for rec in records:
        path = rec["path"]
        dest = os.path.join(staging, *path.split("/"))
        root = os.path.abspath(staging)
        if os.path.commonpath([root, os.path.abspath(dest)]) != root:
            raise BackupError(f"备份路径越界：{path}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        try:
            with zf.open(infos[path]) as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > rec["size"] or total > MAX_BACKUP_UNCOMPRESSED_BYTES:
                        raise BackupError(f"备份文件实际大小超出声明：{path}")
                    digest.update(chunk)
                    dst.write(chunk)
        except (OSError, zipfile.BadZipFile, zlib.error) as e:
            raise BackupError(f"读取备份文件失败：{path}") from e
        if written != rec["size"]:
            raise BackupError(f"备份文件大小不一致：{path}")
        if digest.hexdigest() != rec["sha256"]:
            raise BackupError(f"备份文件校验失败：{path}")


def _load_json(path: str, label: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BackupError(f"{label} 不是有效 JSON") from e


def _load_archive(path: str) -> dict[int, dict]:
    try:
        with gzip.open(path, "rb") as f:
            raw = f.read(MAX_ARCHIVE_JSON_BYTES + 1)
    except (OSError, EOFError, zlib.error) as e:
        raise BackupError("备份中的版本归档损坏") from e
    if len(raw) > MAX_ARCHIVE_JSON_BYTES:
        raise BackupError("备份中的版本归档解压后过大")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BackupError("备份中的版本归档不是有效 JSON") from e
    if not isinstance(data, dict) or data.get("format") != 1 \
            or not isinstance(data.get("versions"), dict):
        raise BackupError("备份中的版本归档格式不支持")
    if len(data["versions"]) > MAX_ARCHIVED_VERSIONS:
        raise BackupError("备份中的版本归档条目过多")
    out: dict[int, dict] = {}
    for key, value in data["versions"].items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            raise BackupError("备份中的版本归档包含非法版本号")
        if n < 0 or not isinstance(value, dict) \
                or not isinstance(value.get("paragraphs"), list):
            raise BackupError(f"备份中的版本归档 v{n} 已损坏")
        out[n] = value
    return out


def _validate_paper_tree(staging: str, manifest: dict) -> tuple[dict, dict]:
    paper_dir = os.path.join(staging, "paper")
    meta = _load_json(os.path.join(paper_dir, "meta.json"), "meta.json")
    if not isinstance(meta, dict):
        raise BackupError("meta.json 不是对象")
    if meta.get("id") != manifest["paper_id"]:
        raise BackupError("meta.json 与备份清单中的论文 ID 不一致")
    if not isinstance(meta.get("filename"), str) or not meta["filename"].strip():
        raise BackupError("meta.json 缺少文件名")
    current = meta.get("current_version")
    if not isinstance(current, int) or current < 0:
        raise BackupError("meta.json 的当前版本号非法")
    source_type = meta.get("source_type")
    if source_type not in ("pdf", "docx"):
        raise BackupError("meta.json 的原文类型非法")
    original = os.path.join(paper_dir, f"original.{source_type}")
    if not os.path.isfile(original) or os.path.getsize(original) == 0:
        raise BackupError("备份缺少原论文文件")

    parsed = _load_json(os.path.join(paper_dir, "parsed.json"), "parsed.json")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("paragraphs"), list):
        raise BackupError("parsed.json 缺少段落列表")
    if not parsed["paragraphs"]:
        raise BackupError("parsed.json 的段落列表为空")

    loose: dict[int, dict] = {}
    versions_dir = os.path.join(paper_dir, "versions")
    if os.path.isdir(versions_dir):
        for name in os.listdir(versions_dir):
            m = _VERSION_RE.match(f"paper/versions/{name}")
            if not m:
                continue
            n = int(m.group(1))
            data = _load_json(os.path.join(versions_dir, name), name)
            if not isinstance(data, dict) or data.get("version") != n \
                    or not isinstance(data.get("paragraphs"), list):
                raise BackupError(f"版本 v{n} 结构非法")
            loose[n] = data
    archive_path = os.path.join(versions_dir, "archive.json.gz")
    archived = _load_archive(archive_path) if os.path.exists(archive_path) else {}
    all_versions = set(loose) | set(archived)
    if 0 not in all_versions:
        raise BackupError("备份缺少原始版本 v0")
    if current not in all_versions:
        raise BackupError(f"备份缺少当前版本 v{current}")

    for name, label in (
        ("requirement.json", "requirement.json"),
        ("similar.json", "similar.json"),
        ("rewrite_guide.json", "rewrite_guide.json"),
    ):
        path = os.path.join(paper_dir, name)
        if os.path.exists(path) and not isinstance(_load_json(path, label), dict):
            raise BackupError(f"{label} 不是对象")
    analysis_dir = os.path.join(paper_dir, "analysis")
    if os.path.isdir(analysis_dir):
        for name in os.listdir(analysis_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(analysis_dir, name)
            if not isinstance(_load_json(path, name), dict):
                raise BackupError(f"{name} 不是对象")

    return meta, {"loose": len(loose), "archived": len(archived),
                  "total": len(all_versions)}


def restore_backup_archive(file_obj) -> dict:
    """校验并恢复为新的论文 ID；任何失败都不留下半成品论文。"""
    root = store.ensure_root()
    staging = tempfile.mkdtemp(prefix=".restore-", dir=root)
    try:
        try:
            zf = zipfile.ZipFile(file_obj)
        except (zipfile.BadZipFile, OSError) as e:
            raise BackupError("不是有效的 zip 备份文件") from e
        try:
            with zf:
                manifest = _find_manifest(zf)
                infos = _validate_zip_entries(zf, manifest["files"])
                _extract_files(zf, manifest["files"], infos, staging)
        except BackupError:
            raise
        except (zipfile.BadZipFile, zlib.error, OSError) as e:
            raise BackupError("备份文件损坏或无法读取") from e

        meta, stats = _validate_paper_tree(staging, manifest)
        new_pid = store.new_paper_id()
        target = os.path.join(store.root_dir(), new_pid)
        meta = dict(meta)
        meta["id"] = new_pid
        meta["updated_at"] = _now()
        meta["restored_at"] = meta["updated_at"]
        meta["restored_from"] = manifest["paper_id"]
        with open(os.path.join(staging, "paper", "meta.json"),
                  "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        with store.locked():
            if os.path.exists(target):
                raise BackupError("恢复目标论文 ID 冲突，请重试")
            os.replace(os.path.join(staging, "paper"), target)
            try:
                store.rebuild_index()
            except OSError:
                logger.exception("恢复后重建索引失败，将在下次列表请求时自动重建")
        return {
            "paper_id": new_pid,
            "title": meta.get("title") or meta.get("filename") or new_pid,
            "filename": meta.get("filename", ""),
            "current_version": int(meta.get("current_version") or 0),
            "restored_from": manifest["paper_id"],
            "version_count": stats["total"],
            "archived_count": stats["archived"],
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)
