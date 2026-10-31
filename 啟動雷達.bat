@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo 第一次執行：建立 Python 環境並安裝套件...
  python -m venv .venv
)
echo 檢查套件...
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2>nul
  (echo [general]& echo email = "")> "%USERPROFILE%\.streamlit\credentials.toml"
)
.venv\Scripts\python -m streamlit run app.py --browser.gatherUsageStats false
