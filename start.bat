@echo off
title 论文诊断与优化工作台 - 启动器
cd /d "%~dp0"

echo ============================================
echo   论文诊断与优化工作台 启动器  (Web v3.0)
echo ============================================
echo.

REM 1. 定位 Python：优先用户显式安装的 Python311，其次 PATH 中的 python
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_CMD=%~dp0.venv\Scripts\python.exe"
    goto :python_ready
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto :python_ready
)
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH，或在本目录创建 .venv。
    pause
    exit /b 1
)
set "PYTHON_CMD=python"
:python_ready
echo [信息] 使用 Python: %PYTHON_CMD%
echo.

REM 2. 检查依赖是否已安装
"%PYTHON_CMD%" -c "import fastapi, uvicorn, openai, fitz, docx, reportlab" >nul 2>nul
if errorlevel 1 (
    echo [提示] 首次运行，正在安装依赖（需要几分钟）...
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请手动执行：pip install -r requirements.txt
        echo        若提示 Cannot connect to proxy，请关闭系统代理或启动代理软件后重试。
        pause
        exit /b 1
    )
)

REM 3. 检查 8501 端口是否被占用（旧实例未关闭时提示）
netstat -ano | findstr ":8501 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [提示] 检测到 8501 端口已有服务在运行。
    echo        如果这是旧实例，请先关闭旧窗口（按 Ctrl+C），再重新运行本启动器。
    echo.
    pause
    exit /b 1
)

REM 4. 启动 Web 服务（启动完成后浏览器会自动打开，只开一个标签页）
echo 正在启动研究助手，浏览器将自动打开 http://localhost:8501
echo 停止服务：回到本窗口按 Ctrl+C
echo.
"%PYTHON_CMD%" server.py

pause
