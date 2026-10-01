@echo off
title 研究助手智能体系统 - 启动器
cd /d "%~dp0"

echo ============================================
echo   研究助手智能体系统 启动器  (Web v2.0)
echo ============================================
echo.

REM 1. 定位 Python：优先用户显式安装的 Python311，其次 PATH 中的 python
set "PYTHON_CMD="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_CMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH。
        pause
        exit /b 1
    )
    set "PYTHON_CMD=python"
)
echo [信息] 使用 Python: %PYTHON_CMD%
echo.

REM 2. 检查依赖是否已安装
"%PYTHON_CMD%" -c "import fastapi, uvicorn, langgraph, chromadb" >nul 2>nul
if errorlevel 1 (
    echo [提示] 首次运行，正在安装依赖（需要几分钟）...
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请手动执行：pip install -r requirements.txt
        pause
        exit /b 1
    )
)

REM 3. 检查 8501 端口是否被占用（旧实例未关闭时提示）
netstat -ano | findstr ":8501 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [提示] 检测到 8501 端口已有服务在运行。
    echo        如果这是旧实例，请先关闭旧窗口（Ctrl+C），再重新启动。
    echo.
)

REM 4. 启动 Web 服务（启动完成后浏览器会自动打开，只开一个标签页）
echo 正在启动研究助手，浏览器将自动打开 http://localhost:8501
echo 停止服务：回到本窗口按 Ctrl+C
echo.
"%PYTHON_CMD%" server.py

pause
