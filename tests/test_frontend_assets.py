# -*- coding: utf-8 -*-
"""前端资源版本化：哈希、幂等、清单、过期检测与语法校验。"""
import json
import os
import shutil

import pytest

from utils import frontend_assets


def _web(tmp_path, index=None):
    web = tmp_path / "web"
    web.mkdir()
    (web / "style.css").write_text("body { color: #111; }\n", encoding="utf-8")
    (web / "app.js").write_text("window.APP = 1;\n", encoding="utf-8")
    (web / "index.html").write_text(
        index or (
            '<!DOCTYPE html>\n'
            '<link rel="stylesheet" href="/style.css?v=0">\n'
            '<script src="/app.js"></script>\n'
        ),
        encoding="utf-8",
    )
    return web


def test_build_stamps_content_hash_and_manifest(tmp_path):
    web = _web(tmp_path)
    result = frontend_assets.build(str(web), check_js=False)

    assert result["changed"] is True
    html = (web / "index.html").read_text(encoding="utf-8")
    css_sha = frontend_assets.digest(str(web / "style.css"))
    js_sha = frontend_assets.digest(str(web / "app.js"))
    assert f'href="/style.css?v={css_sha}"' in html
    assert f'src="/app.js?v={js_sha}"' in html

    manifest = json.loads((web / "asset-manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == {"style.css": css_sha, "app.js": js_sha}
    assert manifest["version"] == result["version"]
    assert frontend_assets.verify(str(web)) == []


def test_build_is_idempotent(tmp_path):
    web = _web(tmp_path)
    first = frontend_assets.build(str(web), check_js=False)
    second = frontend_assets.build(str(web), check_js=False)
    assert first["version"] == second["version"]
    assert second["changed"] is False


def test_verify_reports_missing_or_stale_versions(tmp_path):
    web = _web(tmp_path)
    assert frontend_assets.verify(str(web)) == ["style.css", "app.js"]

    frontend_assets.build(str(web), check_js=False)
    (web / "app.js").write_text("window.APP = 2;\n", encoding="utf-8")
    assert frontend_assets.verify(str(web)) == ["app.js"]


def test_missing_referenced_asset_fails(tmp_path):
    web = _web(tmp_path, (
        '<link rel="stylesheet" href="/missing.css">\n'
        '<script src="/app.js"></script>\n'
    ))
    with pytest.raises(frontend_assets.FrontendBuildError, match="missing.css"):
        frontend_assets.build(str(web), check_js=False)


def test_index_without_local_assets_fails(tmp_path):
    web = _web(tmp_path, "<p>无脚本页面</p>\n")
    with pytest.raises(frontend_assets.FrontendBuildError, match="没有找到"):
        frontend_assets.build(str(web), check_js=False)


def test_invalid_js_is_rejected_when_node_available(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node 不可用")
    web = _web(tmp_path)
    (web / "app.js").write_text("function {\n", encoding="utf-8")
    with pytest.raises(frontend_assets.FrontendBuildError, match="app.js"):
        frontend_assets.build(str(web), check_js=True)


def test_repo_web_assets_are_versioned():
    web_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web")
    assert frontend_assets.scan(web_dir), "index.html 应引用本地 CSS/JS"
    assert frontend_assets.verify(web_dir) == [], (
        "web/index.html 资源版本与内容不一致，请运行 "
        "python scripts/build_frontend.py"
    )
