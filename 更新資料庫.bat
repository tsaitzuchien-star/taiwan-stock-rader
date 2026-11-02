@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv (
  .venv\Scripts\python -c "" >nul 2>&1 || (
    echo 偵測到從別台電腦複製過來的 Python 環境，自動重新建立...
    rmdir /s /q .venv
  )
)
if not exist .venv (
  python --version >nul 2>&1 || (
    echo 找不到 Python：請到 https://www.python.org/downloads/ 安裝，安裝時勾選 Add python.exe to PATH
    pause
    exit /b 1
  )
)
if not exist .venv (
  python -m venv .venv
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python -m radar.collect
pause
