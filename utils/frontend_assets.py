"""极简前端资源版本化：为 index.html 的本地 CSS/JS 写入内容哈希。

不打包、不引入前端框架，只保证浏览器与代理拿到与内容一致的 `?v=` 版本；
构建幂等，并可用 `verify()` 检查仓库内 index.html 是否与资源内容同步。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid

_ASSET_REF = re.compile(
    r'(?P<attr>href|src)="/(?P<name>[A-Za-z0-9_.-]+\.(?:css|js))'
    r'(?:\?v=[^"]*)?"'
)
class FrontendBuildError(RuntimeError):
    """资源缺失、JS 语法错误或写入失败。"""


def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _manifest(files: dict[str, str]) -> dict:
    joined = "".join(f"{name}:{sha}" for name, sha in sorted(files.items()))
    version = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    return {"version": version, "files": files}


def scan(web_dir: str) -> list[str]:
    """返回 index.html 引用的本地 CSS/JS 资源名（保持出现顺序）。"""
    index = os.path.join(web_dir, "index.html")
    try:
        with open(index, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        raise FrontendBuildError(f"无法读取 index.html：{e}") from e
    return [m.group("name") for m in _ASSET_REF.finditer(html)]


def verify(web_dir: str) -> list[str]:
    """返回版本号缺失或与内容不一致的资源名；空列表表示已是最新。"""
    index = os.path.join(web_dir, "index.html")
    try:
        with open(index, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return []
    stale = []
    for m in _ASSET_REF.finditer(html):
        name = m.group("name")
        path = os.path.join(web_dir, name)
        if not os.path.isfile(path):
            stale.append(name)
            continue
        expected = f'{name}?v={digest(path)}'
        if expected not in m.group(0):
            stale.append(name)
    return stale


def _check_js(web_dir: str, names: list[str]) -> str:
    """用 node --check 校验 JS 语法；环境没有 node 时返回 skipped。"""
    node = shutil.which("node")
    if not node:
        return "skipped"
    for name in names:
        if not name.endswith(".js"):
            continue
        result = subprocess.run(
            [node, "--check", os.path.join(web_dir, name)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:600]
            raise FrontendBuildError(f"{name} 语法检查失败：{detail}")
    return "ok"


def build(web_dir: str, check_js: bool = True) -> dict:
    """重写 index.html 的资源版本并写出 asset-manifest.json；幂等。"""
    index = os.path.join(web_dir, "index.html")
    try:
        with open(index, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        raise FrontendBuildError(f"无法读取 index.html：{e}") from e

    files: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        name = match.group("name")
        path = os.path.join(web_dir, name)
        if not os.path.isfile(path):
            raise FrontendBuildError(f"index.html 引用的资源不存在：{name}")
        files[name] = digest(path)
        return f'{match.group("attr")}="/{name}?v={files[name]}"'

    new_html = _ASSET_REF.sub(replace, html)
    if not files:
        raise FrontendBuildError("index.html 中没有找到本地 CSS/JS 引用")

    manifest = _manifest(files)
    js_status = _check_js(web_dir, list(files)) if check_js else "skipped"

    changed = new_html != html
    if changed:
        _write_atomic(index, new_html)
    _write_atomic(
        os.path.join(web_dir, "asset-manifest.json"),
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "version": manifest["version"],
        "files": dict(manifest["files"]),
        "changed": changed,
        "js_check": js_status,
    }
