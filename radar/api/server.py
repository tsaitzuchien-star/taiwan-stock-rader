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
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import alpha as radar_alpha
from .. import arena as radar_arena
from .. import arena_journal as radar_journal
from .. import entry as radar_entry
from .. import trust as radar_trust
from .. import watch as radar_watch
from .. import collect as radar_collect
from .. import positions as radar_positions
from ..config import ROOT, Settings, load_settings
from ..defense import evaluate as defense_evaluate
from ..indicators import compute_metrics, to_ohlcv
from ..storage import Store
from ..tactics import TACTIC_BY_ID, TACTICS, ScanParams, apply_filters
from ..yuanta.client import YuantaError
from .yuanta_hub import YuantaHub

WEB_DIR = ROOT / "web"
SPARK_DAYS = 20

# 指標欄位 -> 前端用的英文鍵
COLMAP = {
    '股票代號': 'code', '股票名稱': 'name', '現價(元)': 'price', '今日漲跌(%)': 'chg',
    '日內振幅(%)': 'amp', '五日均量(張)': 'vol5', '月均量(20日)': 'vol20', '今日成交(張)': 'vol',
    '月量爆發倍數': 'vr', '多空力道': 'power', '成長K線': 'growth', '跳空缺口(%)': 'gap',
    '布林帶寬(%)': 'bw', '均線糾結(%)': 'ma_diff', '季線位置': 'ma60', '強力洗盘訊號': 'washout',
    '季線乖離(%)': 'bias', '20日漲幅(%)': 'ret20', '前期布林寬(%)': 'pre_bw', '_close_pos': 'close_pos',
}


class Engine:
    """把資料庫與指標計算包起來；同一個交易日的計算結果會快取。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.lock = threading.RLock()
        self._cache: dict | None = None
        self._alpha: dict | None = None
        self.update_job: dict = {"running": False}
        self.arena_job: dict = {"running": False}
        self.yuanta = YuantaHub(settings)
        self._arena_lock = threading.Lock()

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
            self._alpha = None

    def chip_data(self) -> dict:
        with self.lock:
            return {k: self.store.dataset(k) for k in ("inst", "margin", "valuation", "revenue", "company")}

    def alpha(self) -> dict:
        """法人選股因子表（依日 K 與籌碼資料的最新日期快取）。"""
        cache = self.metrics()
        with self.lock:
            key = (cache["latest"],) + tuple(self.store.latest_dataset_date(k)
                                             for k in ("inst", "margin", "valuation", "revenue"))
            if self._alpha and self._alpha["key"] == key:
                return self._alpha
            data = self.chip_data()
            df = radar_alpha.build(cache["df"], cache["bars"], data["inst"], data["margin"],
                                   data["valuation"], data["revenue"], data["company"], cache["markets"])
            self._alpha = {"key": key, "df": df, "inst": data["inst"], "margin": data["margin"],
                           "revenue": data["revenue"],
                           "coverage": {"inst_date": key[1], "margin_date": key[2], "valuation_date": key[3],
                                        "revenue_ym": key[4],
                                        "with_chips": int(df["f5"].notna().sum()) if not df.empty else 0}}
            return self._alpha


    def trust_predict(self) -> dict:
        """投信明日續買預測（依法人資料與日 K 的最新日期快取）。"""
        cache = self.metrics()
        with self.lock:
            key = (cache["latest"], self.store.latest_dataset_date("inst"))
            if getattr(self, "_trust", None) and self._trust["key"] == key:
                return self._trust["data"]
            inst = self.store.dataset("inst")
            names = dict(zip(*self.store.stocks()[["code", "name"]].T.values))
        data = radar_trust.predict(inst, cache["bars"], names)
        with self.lock:
            self._trust = {"key": key, "data": data}
        return data

    def alpha_lookup(self) -> dict[str, dict]:
        """進場分數用的法人評分；籌碼資料有問題時不要讓掃描整個失敗。"""
        try:
            return radar_entry.alpha_lookup(self.alpha()["df"])
        except Exception:  # noqa: BLE001
            return {}

    # ---------------------------------------------------------------- 百萬操盤擂台
    def arena_refresh(self) -> None:
        """有新交易日就在背景補算（第一次開賽要回測幾週，約需數十秒）。"""
        if self.update_job.get("running") or not self._arena_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                pending = radar_arena.Arena(self.store).pending()
        except Exception:  # noqa: BLE001
            self._arena_lock.release()
            raise
        if not pending:
            self._arena_lock.release()
            return
        self.arena_job.update({"running": True, "error": None})

        def run():
            store = Store(self.settings.db_path)   # 自己的連線，不卡住畫面查詢
            try:
                radar_arena.Arena(store).run()
                self.arena_export(store)
            except Exception as e:  # noqa: BLE001
                self.arena_job["error"] = str(e)
            finally:
                store.close()
                self.arena_job["running"] = False
                self._arena_lock.release()
        threading.Thread(target=run, daemon=True).start()

    def arena_export(self, store=None) -> None:
        """每日日誌、週報、CSV 自動存到 data/擂台紀錄（存檔失敗不影響擂台）。"""
        try:
            if store is None:
                with self.lock:
                    path = radar_journal.export(self.store, radar_journal.log_dir(self.settings))
            else:
                path = radar_journal.export(store, radar_journal.log_dir(self.settings))
            self.arena_job.update({"log_dir": str(path) if path else None, "export_error": None})
        except Exception as e:  # noqa: BLE001
            self.arena_job["export_error"] = str(e)

    def arena_report(self) -> dict:
        self.arena_refresh()
        if not self.arena_job.get("running") and "log_dir" not in self.arena_job:
            self.arena_export()
        with self.lock:
            rep = radar_arena.Arena(self.store).report()
        rep["running"] = self.arena_job.get("running", False)
        rep["error"] = self.arena_job.get("error")
        rep["log_dir"] = self.arena_job.get("log_dir")
        rep["export_error"] = self.arena_job.get("export_error")
        return rep

    def arena_journal(self, kind: str, key: str | None):
        if self.arena_job.get("running"):
            return {"ready": False, "running": True}
        with self.lock:
            j = radar_journal.Journal(self.store)
            out = j.day(key) if kind == "day" else j.week(key)
            if kind == "day" and out.get("ready"):
                out["days"] = list(reversed(j.days))
        return radar_arena._clean(out)

    def arena_reset(self, fresh: bool = False) -> dict:
        if self.arena_job.get("running"):
            raise HTTPException(409, "擂台正在計算中，請稍後再試")
        with self.lock:
            radar_arena.Arena(self.store).reset(fresh=fresh)
        self.arena_job.pop("log_dir", None)
        return self.arena_report()


class ScanRequest(BaseModel):
    tactic: str | None = None
    params: dict | None = None


class WatchRequest(BaseModel):
    code: str
    source: str | None = None
    note: str | None = None


def df_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON 列（NaN 轉成 null）。"""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _lots(shares) -> int:
    return int(round(shares / 1000)) if pd.notna(shares) else 0


ALPHA_COLS = ["rank", "code", "name", "market", "industry", "price", "chg", "vol20", "score", "s_chip",
              "s_momentum", "s_growth", "s_value", "f1", "t1", "d1", "f5", "t5", "f20", "t20", "f_streak",
              "t_streak", "legal5_ratio", "trust5_ratio", "margin_bal", "margin_chg5", "short_ratio", "ret20",
              "ret60", "dist_ma60", "pe", "pb", "dy", "rev_ym", "yoy", "mom", "cum_yoy", "tags"]


TF_RULES = {"W": "W-FRI", "M": "ME"}


def resample_ohlcv(ohlcv: pd.DataFrame, tf: str) -> pd.DataFrame:
    """日 K 轉週 K / 月 K（時間標在該週、該月最後一個交易日）。"""
    rule = TF_RULES.get((tf or "D").upper())
    if not rule or ohlcv.empty:
        return ohlcv
    idx = pd.Series(ohlcv.index, index=ohlcv.index)
    g = ohlcv.resample(rule)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    out.index = idx.resample(rule).last()
    return out.dropna(subset=["Close"])


def rows_json(df: pd.DataFrame, cache: dict, alpha: dict[str, dict] | None = None) -> list[dict]:
    if df.empty:
        return []
    out = df.rename(columns=COLMAP)
    records = json.loads(out.to_json(orient="records", force_ascii=False))
    for r in records:
        r.update(radar_entry.score_row(r, (alpha or {}).get(r["code"])))
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

    @app.middleware("http")
    async def _no_stale_web(request, call_next):
        """網頁檔案每次都跟伺服器確認版本，更新程式後不用手動清瀏覽器暫存。"""
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or (path.startswith("/static/") and "/vendor/" not in path):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.on_event("startup")
    def _auto_connect_yuanta():
        """有設定元大帳號就在背景自動登入一次（失敗不重試，避免用掉每日登入次數）。"""
        hub = engine.yuanta
        if hub.configured and not hub.config.problems():
            def run():
                try:
                    hub.connect()
                except Exception:  # noqa: BLE001
                    pass   # 錯誤訊息存在 hub.error，持倉風控頁會顯示

            def watchdog():   # 元大清晨會踢掉連線：白天每分鐘檢查一次，斷了就自動接回
                while True:
                    time.sleep(60)
                    try:
                        hub.auto_reconnect()
                    except Exception:  # noqa: BLE001
                        pass
            threading.Thread(target=run, daemon=True).start()
            threading.Thread(target=watchdog, daemon=True).start()

    @app.on_event("shutdown")
    def _logout_yuanta():
        try:
            engine.yuanta.disconnect()
        except Exception:  # noqa: BLE001
            pass

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
                "params": cfg.to_dict(), "rows": rows_json(res, cache, engine.alpha_lookup())}

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
        live = _live_breadth(cache, str(latest.date()))
        if live:
            return live
        today = bars[bars["date"] == latest].set_index("code")["close"]
        out = _count(((close, cache["prev_close"].get(code)) for code, close in today.items()))
        df = cache["df"]
        above = float((df['現價(元)'] >= df['季線位置']).mean() * 100) if not df.empty else None
        return {"latest": str(latest.date()), **out, "above_ma60_pct": above, "live": False}

    def _count(pairs) -> dict:
        up = down = flat = lim_up = lim_down = 0
        for close, prev in pairs:
            if not prev or close is None:
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
        return {"up": up, "down": down, "flat": flat, "limit_up": lim_up, "limit_down": lim_down}

    def _live_breadth(cache: dict, db_latest: str) -> dict | None:
        """元大已連線、今天是交易日、資料庫還沒有今天的盤後資料：改用元大即時報價算大盤。"""
        hub = engine.yuanta
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if not hub.logged_in or now.weekday() >= 5 or db_latest >= today or now.strftime("%H:%M") < "09:00":
            return None
        df = cache["df"]
        if df.empty:
            return None
        markets = cache["markets"]
        hub.refresh_market([(c, markets.get(c) or "TWSE") for c in df['股票代號']])
        quotes = hub.quotes()
        rows = [(quotes.get(c, {}).get("price"), quotes.get(c, {}).get("prev_close"), ma60)
                for c, ma60 in zip(df['股票代號'], df['季線位置'])]
        rows = [r for r in rows if r[0] and r[1]]
        if len(rows) < len(df) * 0.5:   # 快照還沒抓完：先顯示盤後資料
            return None
        out = _count((p, prev) for p, prev, _ in rows)
        valid = [(p, m) for p, _, m in rows if m == m and m]
        above = sum(p >= m for p, m in valid) / len(valid) * 100 if valid else None
        closed = now.strftime("%H:%M") >= "13:31"
        return {"latest": today, **out, "above_ma60_pct": above, "live": True, "closed": closed,
                "time": hub.market_time, "count": len(rows), "db_latest": db_latest}

    @app.get("/api/stock/{code}")
    def stock(code: str, tf: str = "D"):
        cache = engine.metrics()
        bars = cache["bars"]
        g = bars[bars["code"] == code]
        if g.empty:
            raise HTTPException(404, f"資料庫裡沒有 {code}")
        names = engine.stocks().set_index("code")["name"].to_dict()
        ohlcv = resample_ohlcv(to_ohlcv(g), tf)
        close = ohlcv["Close"]
        ma = {n: close.rolling(n).mean() for n in (5, 20, 60)}
        std20 = close.rolling(20).std()
        candles = [{"time": d.strftime("%Y-%m-%d"), "open": r.Open, "high": r.High, "low": r.Low,
                    "close": r.Close, "volume": int(r.Volume / 1000)} for d, r in ohlcv.iterrows()]

        def series(s):
            return [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
                    for d, v in s.items() if pd.notna(v)]
        row = cache["df"][cache["df"]["股票代號"] == code]
        metrics = rows_json(row, cache, engine.alpha_lookup())[0] if not row.empty else None
        return {"code": code, "name": names.get(code, ""), "market": cache["markets"].get(code, ""),
                "candles": candles, "ma5": series(ma[5]), "ma20": series(ma[20]), "ma60": series(ma[60]),
                "bb_upper": series(ma[20] + 2 * std20), "bb_lower": series(ma[20] - 2 * std20),
                "metrics": metrics, "tf": tf.upper() if tf.upper() in TF_RULES else "D",
                "history_days": len(g)}

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

    @app.get("/api/alpha")
    def alpha(preset: str = "all", industry: str = "", market: str = "", min_vol20: float = radar_alpha.MIN_VOL20,
              limit: int = 300):
        a = engine.alpha()
        df = radar_alpha.apply_preset(a["df"], preset, min_vol20)
        if industry:
            df = df[df["industry"] == industry]
        if market:
            df = df[df["market"] == market]
        industries = sorted(a["df"]["industry"].dropna().unique().tolist()) if not a["df"].empty else []
        return {"presets": radar_alpha.PRESETS, "weights": radar_alpha.WEIGHTS, "coverage": a["coverage"],
                "industries": industries, "total": len(df),
                "rows": df_records(df[ALPHA_COLS].head(limit)) if not df.empty else []}

    @app.get("/api/alpha/sectors")
    def alpha_sectors():
        return radar_alpha.sectors(engine.alpha()["df"])

    @app.get("/api/market/flows")
    def market_flows():
        a = engine.alpha()
        return radar_alpha.market_flows(a["inst"], engine.metrics()["bars"])

    @app.get("/api/stock/{code}/chips")
    def stock_chips(code: str):
        a = engine.alpha()
        df = a["df"]
        row = df[df["code"] == code] if not df.empty else df
        inst = a["inst"][a["inst"]["code"] == code].sort_values("date")
        margin = a["margin"][a["margin"]["code"] == code].sort_values("date")
        rev = a["revenue"][a["revenue"]["code"] == code].sort_values("ym")
        return {
            "code": code,
            "score": df_records(row[ALPHA_COLS])[0] if not row.empty else None,
            "universe": len(df),
            "inst": [{"date": r.date, "foreign": _lots(r.foreign_net), "trust": _lots(r.trust_net),
                      "dealer": _lots(r.dealer_net)} for r in inst.itertuples()],
            "margin": df_records(margin[["date", "margin_bal", "short_bal"]]),
            "revenue": df_records(rev),
            "coverage": a["coverage"],
        }

    # ------------------------------------------------------------ 元大（唯讀）與持倉風控
    @app.get("/api/yuanta/status")
    def yuanta_status():
        return engine.yuanta.status()

    @app.post("/api/yuanta/connect")
    def yuanta_connect():
        try:
            return engine.yuanta.connect()
        except YuantaError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/yuanta/disconnect")
    def yuanta_disconnect():
        return engine.yuanta.disconnect()

    @app.get("/api/positions")
    def positions(refresh: bool = False):
        hub = engine.yuanta
        try:
            h = hub.holdings(force=refresh)
        except YuantaError as e:
            raise HTTPException(409 if not hub.logged_in else 502, str(e)) from e
        bars = engine.metrics()["bars"]
        codes = set(h["股票代號"])
        closes = {}
        if not bars.empty and codes:
            sub = bars[bars["code"].isin(codes)]
            for code, g in sub.groupby("code"):
                closes[code] = pd.Series(g["close"].values, index=g["date"].dt.strftime("%Y-%m-%d"))
        out = radar_positions.build(h, hub.quotes(), closes, datetime.now().strftime("%Y-%m-%d"))
        out["holdings_time"] = hub.holdings_time
        out["now"] = datetime.now().strftime("%H:%M:%S")
        return radar_arena._clean(out)

    @app.get("/api/positions/realized")
    def positions_realized(days: int = 30):
        try:
            return radar_arena._clean(radar_positions.realized_summary(engine.yuanta.realized(min(max(days, 1), 365))))
        except YuantaError as e:
            raise HTTPException(409 if not engine.yuanta.logged_in else 502, str(e)) from e

    @app.get("/api/trust/predict")
    def trust_predict():
        return engine.trust_predict()

    # ---------------------------------------------------------------- 觀察名單
    @app.get("/api/watch")
    def watch_list():
        cache = engine.metrics()
        with engine.lock:
            rep = radar_watch.Watch(engine.store).report(cache["bars"])
        rep["live"] = None
        hub, now = engine.yuanta, datetime.now()
        today = now.strftime("%Y-%m-%d")
        if (rep["rows"] and hub.logged_in and now.weekday() < 5 and str(cache["latest"])[:10] < today
                and now.strftime("%H:%M") >= "09:00"):
            # 盤中：至今報酬改用元大即時報價（全市場快照每分鐘更新，持股另有即時推播）
            markets = cache["markets"]
            hub.refresh_market([(c, markets.get(c) or "TWSE") for c in cache["df"]["股票代號"]])
            n = radar_watch.apply_live(rep["rows"], hub.quotes(), today)
            if n:
                rep["live"] = {"time": hub.market_time, "count": n,
                               "closed": now.strftime("%H:%M") >= "13:31"}
        return rep

    @app.post("/api/watch")
    def watch_add(req: WatchRequest):
        cache = engine.metrics()
        row = cache["df"][cache["df"]["股票代號"] == req.code]
        if row.empty:
            raise HTTPException(404, f"{req.code} 最新一日沒有資料，無法記錄")
        m = rows_json(row, cache, engine.alpha_lookup())[0]
        item = {"code": m["code"], "name": m["name"], "added_date": cache["latest"], "price": m["price"],
                "entry": m["entry"], "parts": m["entry_parts"], "stage": m["stage"], "light": m["light"],
                "source": (req.source or "手動").strip()[:20], "note": req.note}
        with engine.lock:
            item["id"] = radar_watch.Watch(engine.store).add(item)
        return item

    @app.delete("/api/watch/{wid}")
    def watch_remove(wid: int):
        with engine.lock:
            if not radar_watch.Watch(engine.store).remove(wid):
                raise HTTPException(404, "找不到這筆觀察")
        return {"ok": True}

    @app.get("/api/arena")
    def arena():
        return engine.arena_report()

    @app.get("/api/arena/journal")
    def arena_journal(date: str | None = None):
        return engine.arena_journal("day", date)

    @app.get("/api/arena/week")
    def arena_week(start: str | None = None):
        return engine.arena_journal("week", start)

    @app.get("/api/arena/week.md")
    def arena_week_md(start: str | None = None):
        w = engine.arena_journal("week", start)
        name = f"擂台週報_{w.get('start', '')}.md"
        from urllib.parse import quote
        return Response(radar_journal.week_md(w), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/arena/day.md")
    def arena_day_md(date: str | None = None):
        j = engine.arena_journal("day", date)
        from urllib.parse import quote
        return Response(radar_journal.day_md(j), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('擂台日誌_' + str(j.get('date', '')) + '.md')}"})

    @app.post("/api/arena/reset")
    def arena_reset(fresh: bool = False):
        return engine.arena_reset(fresh=fresh)

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
                engine.arena_refresh()   # 新的交易日：擂台自動補算
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
