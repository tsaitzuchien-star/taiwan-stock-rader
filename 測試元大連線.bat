@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo ===== 元大連線測試（只查詢庫存與報價，不會下單） =====
.venv\Scripts\python -m radar.yuanta.check
echo.
pause
