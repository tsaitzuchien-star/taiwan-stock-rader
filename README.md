# 台股妖股雷達

以證交所、櫃買中心官方盤後資料為核心的台股掃描工具。全市場日 K 先存進本地資料庫，掃描直接讀庫，幾秒內完成。

## Windows 使用方式

1. 安裝 [Python 3.11 以上](https://www.python.org/downloads/)，安裝時勾選「Add Python to PATH」。
2. 下載本專案後，雙擊 **`啟動雷達.bat`**。第一次會自動安裝套件，之後瀏覽器會打開雷達畫面。
3. 第一次使用請在畫面上方「📦 本地資料庫」按 **「⬇️ 更新資料庫」**，回補約 90 個交易日（約 5～10 分鐘）。
4. 之後每個交易日 14:30 後按一次更新（只要幾秒），或雙擊 **`更新資料庫.bat`**，也可以用 Windows「工作排程器」每天自動執行。

## 個人設定

把 `config.example.toml` 複製成 `config.local.toml` 再修改（總資金、單檔曝險比例等）。
`config.local.toml`、金鑰、憑證都在 `.gitignore` 裡，不會上傳 GitHub。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `app.py` | Streamlit 介面（三個艙） |
| `radar/collectors/` | 外部資料來源：TWSE、TPEx（之後加元大 Spark API） |
| `radar/storage.py` | SQLite 本地資料庫 |
| `radar/collect.py` | 盤後批次收集器（`python -m radar.collect`） |
| `radar/indicators.py` | 雷達指標計算 |
| `tests/` | 單元測試（`python -m pytest`） |
