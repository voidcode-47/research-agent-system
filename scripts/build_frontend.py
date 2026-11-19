"""为 web/index.html 的本地 CSS/JS 写入内容哈希版本。

用法:
    python scripts/build_frontend.py
    python scripts/build_frontend.py --no-js-check
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.frontend_assets import FrontendBuildError, build  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="前端资源版本化（无打包、无框架）")
    parser.add_argument("--no-js-check", action="store_true",
                        help="跳过 node --check 语法校验")
    args = parser.parse_args()

    web_dir = os.path.join(ROOT, "web")
    try:
        result = build(web_dir, check_js=not args.no_js_check)
    except FrontendBuildError as e:
        print(f"[失败] {e}")
        return 1

    print(f"asset version: {result['version']}")
    for name, sha in sorted(result["files"].items()):
        print(f"  {name}: {sha}")
    print(f"index.html: {'已更新' if result['changed'] else '无需变更'}")
    print(f"JS 语法检查: {result['js_check']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
