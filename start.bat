@echo off
title ����������Ż�����̨ - ������
cd /d "%~dp0"

echo ============================================
echo   ����������Ż�����̨ ������  (Web v3.0)
echo ============================================
echo.

REM 1. ��λ Python�������û���ʽ��װ�� Python311����� PATH �е� python
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
    echo [����] δ�ҵ� Python�����Ȱ�װ Python 3.10+ ������ PATH�����ڱ�Ŀ¼���� .venv��
    pause
    exit /b 1
)
set "PYTHON_CMD=python"
:python_ready
echo [��Ϣ] ʹ�� Python: %PYTHON_CMD%
echo.

REM 2. ��������Ƿ��Ѱ�װ
"%PYTHON_CMD%" -c "import fastapi, uvicorn, openai, fitz, docx, reportlab" >nul 2>nul
if errorlevel 1 (
    echo [��ʾ] �״����У����ڰ�װ��������Ҫ�����ӣ�...
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [����] ������װʧ�ܣ����ֶ�ִ�У�pip install -r requirements.txt
        echo        ����ʾ Cannot connect to proxy����ر�ϵͳ�����������������������ԡ�
        pause
        exit /b 1
    )
)

REM 3. ��� 8501 �˿��Ƿ�ռ�ã���ʵ��δ�ر�ʱ��ʾ��
netstat -ano | findstr ":8501 " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo [��ʾ] ��⵽ 8501 �˿����з��������С�
    echo        ������Ǿ�ʵ�������ȹرվɴ��ڣ��� Ctrl+C�������������б���������
    echo.
    pause
    exit /b 1
)

REM 4. ���� Web ����������ɺ���������Զ��򿪣�ֻ��һ����ǩҳ��
echo ���������о����֣���������Զ��� http://localhost:8501
echo ֹͣ���񣺻ص������ڰ� Ctrl+C
echo.
"%PYTHON_CMD%" server.py

pause
