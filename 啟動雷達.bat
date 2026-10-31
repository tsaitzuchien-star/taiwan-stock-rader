@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo 第一次執行：建立 Python 環境並安裝套件...
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m streamlit run app.py
