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
  echo 第一次執行：建立 Python 環境並安裝套件...
  python -m venv .venv
)
echo 檢查套件...
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo 台股雷達 戰情室啟動中，瀏覽器會自動打開 http://127.0.0.1:8600
echo 使用期間請不要關閉這個黑色視窗；要結束時直接關掉即可。
echo.
.venv\Scripts\python -m radar.api
pause
