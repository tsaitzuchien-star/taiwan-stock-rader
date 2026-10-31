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

## 元大 Spark API（第四艙、盤中模式）

本程式只「查詢」元大資料：庫存、未實現 / 已實現損益、即時報價。**沒有任何下單功能。**

1. 安裝 [.NET 8 Runtime](https://dotnet.microsoft.com/download/dotnet/8.0)（Windows x64，元大元件需要）。
2. 確認元大憑證已安裝在這台電腦（元大「憑證e總管」/ YuantaServiSign 完成憑證下載即可）。
3. 在 `config.local.toml` 加入 `[yuanta]` 區段（格式見 `config.example.toml`）：帳號、電子密碼、`sdk_dir`（放 `YuantaSparkAPI.dll` 的資料夾）。
4. 雙擊 **`測試元大連線.bat`**：會登入並列出庫存與 2330 報價，確認連線正常。
5. 啟動雷達後到「💼 第四艙」按「連線登入元大」。登入後第一艙可勾選「⚡ 盤中模式」，用元大即時報價當作今天的 K 棒做全市場掃描。

程式內建元大安控限制（同功能每秒 ≤3 次、K 線每秒 1 次、訂閱 ≤1900 檔、報價表每批 500 檔），
收到「使用限制」通知會自動停手，避免被停權。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `app.py` | Streamlit 介面（四個艙） |
| `radar/collectors/` | 外部資料來源：TWSE、TPEx 官方盤後資料 |
| `radar/yuanta/` | 元大 Spark API 唯讀用戶端（`python -m radar.yuanta.check` 連線測試） |
| `radar/ui_yuanta.py` | 第四艙介面、盤中模式 |
| `radar/storage.py` | SQLite 本地資料庫 |
| `radar/collect.py` | 盤後批次收集器（`python -m radar.collect`） |
| `radar/indicators.py` | 雷達指標計算 |
| `tests/` | 單元測試（`python -m pytest`） |
