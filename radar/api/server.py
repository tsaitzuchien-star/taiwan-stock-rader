"""戰情室後端 API。資料來源：本地 SQLite（官方盤後資料），之後加上元大即時資料。

所有回應都是 JSON；網頁前端放在專案的 web/ 資料夾，由同一個程式提供。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import collect as radar_collect
from ..config import ROOT, Settings, load_settings
from ..defense import evaluate as defense_evaluate
from ..indicators import compute_metrics, to_ohlcv
from ..storage import Store
from ..tactics import TACTIC_BY_ID, TACTICS, ScanParams, apply_filters

WEB_DIR = ROOT / "web"
SPARK_DAYS = 20

# 指標欄位 -> 前端用的英文鍵
COLMAP = {
    '股票代號': 'code', '股票名稱': 'name', '現價(元)': 'price', '今日漲跌(%)': 'chg',
    '日內振幅(%)': 'amp', '五日均量(張)': 'vol5', '月均量(20日)': 'vol20', '今日成交(張)': 'vol',
    '月量爆發倍數': 'vr', '多空力道': 'power', '成長K線': 'growth', '跳空缺口(%)': 'gap',
    '布林帶寬(%)': 'bw', '均線糾結(%)': 'ma_diff', '季線位置': 'ma60', '強力洗盘訊號': 'washout',
}


class Engine:
    """把資料庫與指標計算包起來；同一個交易日的計算結果會快取。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.lock = threading.RLock()
        self._cache: dict | None = None
        self.update_job: dict = {"running": False}

    def health(self) -> dict:
        with self.lock:
            h = self.store.health()
        errors = h.pop("errors")
        h["recent_errors"] = json.loads(errors.to_json(orient="records", force_ascii=False))
        return h

    def stocks(self) -> pd.DataFrame:
        with self.lock:
            return self.store.stocks()

    def metrics(self) -> dict:
        """全市場指標（依最新交易日快取）。"""
        with self.lock:
            latest = self.store.health()["latest_date"]
            if self._cache and self._cache["latest"] == latest:
                return self._cache
            t0 = time.perf_counter()
            stocks = self.store.stocks()
            names = dict(zip(stocks["code"], stocks["name"]))
            markets = dict(zip(stocks["code"], stocks["market"]))
            bars = self.store.bars()
            df, missing = compute_metrics(bars, names)
            sparks = {c: [round(float(x), 2) for x in g["close"].tail(SPARK_DAYS)]
                      for c, g in bars.groupby("code")} if not bars.empty else {}
            prev_close = {}
            if not bars.empty:
                for c, g in bars.groupby("code"):
                    if len(g) >= 2:
                        prev_close[c] = float(g["close"].iloc[-2])
            self._cache = {"latest": latest, "df": df, "missing": missing, "sparks": sparks,
                           "markets": markets, "bars": bars, "prev_close": prev_close,
                           "elapsed": round(time.perf_counter() - t0, 2)}
            return self._cache

    def invalidate(self) -> None:
        with self.lock:
            self._cache = None


class ScanRequest(BaseModel):
    tactic: str | None = None
    params: dict | None = None


def rows_json(df: pd.DataFrame, cache: dict) -> list[dict]:
    if df.empty:
        return []
    out = df.rename(columns=COLMAP)
    records = json.loads(out.to_json(orient="records", force_ascii=False))
    for r in records:
        r["market"] = cache["markets"].get(r["code"], "")
        r["spark"] = cache["sparks"].get(r["code"], [])
        r["growth"] = r.get("growth", "-") != "-"
        r["washout"] = r.get("washout", "-") != "-"
    return records


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = Engine(settings)
    app = FastAPI(title="台股雷達戰情室", docs_url="/api/docs", redoc_url=None)
    app.state.engine = engine

    @app.get("/api/health")
    def health():
        return {"time": datetime.now().isoformat(timespec="seconds"), "db": engine.health(),
                "settings": {"history_trading_days": settings.history_trading_days},
                "update": engine.update_job}

    @app.get("/api/stocks")
    def stocks():
        df = engine.stocks()
        return [{"code": c, "name": n, "market": m} for c, n, m in df.itertuples(index=False)]

    @app.get("/api/tactics")
    def tactics():
        return [{"id": t["id"], "name": t["name"], "desc": t["desc"], "params": t["params"].to_dict()}
                for t in TACTICS]

    @app.post("/api/scan")
    def scan(req: ScanRequest):
        cache = engine.metrics()
        base = TACTIC_BY_ID[req.tactic]["params"] if req.tactic in TACTIC_BY_ID else ScanParams()
        values = base.to_dict()
        for k, v in (req.params or {}).items():
            if k in values:
                values[k] = type(values[k])(v)
        cfg = ScanParams(**values)
        t0 = time.perf_counter()
        res = apply_filters(cache["df"], cfg)
        return {"latest": cache["latest"], "universe": len(cache["df"]), "skipped": len(cache["missing"]),
                "elapsed": round(cache["elapsed"] + time.perf_counter() - t0, 2),
                "params": cfg.to_dict(), "rows": rows_json(res, cache)}

    @app.get("/api/tactics/summary")
    def tactics_summary():
        cache = engine.metrics()
        return [{"id": t["id"], "name": t["name"], "count": len(apply_filters(cache["df"], t["params"]))}
                for t in TACTICS]

    @app.get("/api/market/breadth")
    def breadth():
        cache = engine.metrics()
        bars = cache["bars"]
        if bars.empty:
            return {"latest": None}
        latest = bars["date"].max()
        today = bars[bars["date"] == latest].set_index("code")["close"]
        up = down = flat = lim_up = lim_down = 0
        for code, close in today.items():
            prev = cache["prev_close"].get(code)
            if not prev:
                continue
            chg = (close - prev) / prev * 100
            if chg > 0:
                up += 1
            elif chg < 0:
                down += 1
            else:
                flat += 1
            lim_up += chg >= 9.5
            lim_down += chg <= -9.5
        df = cache["df"]
        above = float((df['現價(元)'] >= df['季線位置']).mean() * 100) if not df.empty else None
        return {"latest": str(latest.date()), "up": up, "down": down, "flat": flat,
                "limit_up": lim_up, "limit_down": lim_down, "above_ma60_pct": above}

    @app.get("/api/stock/{code}")
    def stock(code: str):
        cache = engine.metrics()
        bars = cache["bars"]
        g = bars[bars["code"] == code]
        if g.empty:
            raise HTTPException(404, f"資料庫裡沒有 {code}")
        names = engine.stocks().set_index("code")["name"].to_dict()
        ohlcv = to_ohlcv(g)
        close = ohlcv["Close"]
        ma = {n: close.rolling(n).mean() for n in (5, 20, 60)}
        std20 = close.rolling(20).std()
        candles = [{"time": d.strftime("%Y-%m-%d"), "open": r.Open, "high": r.High, "low": r.Low,
                    "close": r.Close, "volume": int(r.Volume / 1000)} for d, r in ohlcv.iterrows()]

        def series(s):
            return [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
                    for d, v in s.items() if pd.notna(v)]
        row = cache["df"][cache["df"]["股票代號"] == code]
        metrics = rows_json(row, cache)[0] if not row.empty else None
        return {"code": code, "name": names.get(code, ""), "market": cache["markets"].get(code, ""),
                "candles": candles, "ma5": series(ma[5]), "ma20": series(ma[20]), "ma60": series(ma[60]),
                "bb_upper": series(ma[20] + 2 * std20), "bb_lower": series(ma[20] - 2 * std20),
                "metrics": metrics}

    @app.get("/api/stock/{code}/defense")
    def defense(code: str, cost: float, shares: int, fee_discount: float = 1.0):
        bars = engine.metrics()["bars"]
        g = bars[bars["code"] == code]
        if g.empty:
            raise HTTPException(404, f"資料庫裡沒有 {code}")
        try:
            return defense_evaluate(cost, shares, g["close"], fee_discount)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/db/update")
    def db_update():
        job = engine.update_job
        if job.get("running"):
            return job
        job.clear()
        job.update({"running": True, "message": "準備中…", "progress": 0.0,
                    "started": datetime.now().isoformat(timespec="seconds")})

        def run():
            def progress(msg, frac):
                job.update({"message": msg, "progress": round(min(max(frac, 0), 1), 3)})
            try:
                # 收集器用自己的資料庫連線，不卡住畫面查詢
                report = radar_collect.update(Store(settings.db_path), settings, progress=progress)
                job.update({"summary": report.summary(), "errors": report.errors[:20]})
            except Exception as e:  # noqa: BLE001
                job.update({"errors": [str(e)], "summary": "更新失敗"})
            finally:
                engine.invalidate()
                job.update({"running": False, "progress": 1.0,
                            "finished": datetime.now().isoformat(timespec="seconds")})
        threading.Thread(target=run, daemon=True).start()
        return job

    @app.get("/api/db/update")
    def db_update_status():
        return engine.update_job

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app
