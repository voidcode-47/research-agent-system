"""Exercise the Windows launcher through cmd.exe, including Explorer-style paths."""
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")


def test_start_bat_launches_server_from_another_directory(tmp_path):
    app_dir = tmp_path / "启动测试 with spaces"
    app_dir.mkdir()
    shutil.copyfile(ROOT / "start.bat", app_dir / "start.bat")
    venv.EnvBuilder(with_pip=False).create(app_dir / ".venv")

    # Keep dependency checks local and avoid installing packages during the test.
    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("fastapi", "uvicorn", "openai", "fitz", "docx", "reportlab"):
        (modules / f"{name}.py").touch()
    (modules / "pip.py").write_text("raise SystemExit(73)\n", encoding="utf-8")
    (app_dir / "netstat.cmd").write_bytes(b"@echo off\r\nexit /b 0\r\n")
    (app_dir / "server.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parent\n"
        "assert Path.cwd() == root\n"
        "assert Path(sys.executable) == root / '.venv' / 'Scripts' / 'python.exe'\n"
        "print('LAUNCHER_SERVER_STARTED')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(app_dir / "start.bat")],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(modules)},
        input=b"",
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert b"LAUNCHER_SERVER_STARTED" in result.stdout, (
        result.stdout + result.stderr
    ).decode("gb18030", errors="replace")
    assert result.returncode == 0
