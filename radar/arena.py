"""百萬操盤擂台：用雷達的法人評分，每天模擬操作 100 萬元（不是真的下單）。

流程（每個交易日）：
1. 收盤後：用當天為止的資料算出全市場法人評分，決定隔天要買、要賣哪些股票。
2. 隔天開盤：照開盤價成交（開盤漲停買不到、跌停賣不掉），扣手續費與證交稅。
3. 收盤後：用收盤價結算總資產，和 0050 比較，再重複第 1 步。

開賽時會先用資料庫裡有法人資料的前幾週「回測」，之後每天的結果才是「實戰」。
狀態存在本地資料庫，每次更新資料庫或打開戰情室時自動補算到最新交易日。
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from . import alpha

NAME = "百萬操盤擂台"
START_CASH = 1_000_000
MAX_POSITIONS = 8
FEE_RATE = 0.001425
MIN_FEE = 20
TAX_RATE = 0.003
BENCHMARK = "0050"
MIN_HISTORY = 60   # 開賽前至少要有幾天日 K

RULES = {
    "entry": [
        "法人綜合評分 ≥ 75，而且籌碼分數 ≥ 60",
        "月均量 ≥ 1,000 張（流動性夠，進出不卡）",
        "收盤站上季線，當天漲幅 < 7%（不追漲停）",
        "依總分由高到低補滿 8 檔，每檔約總資產的 1/8",
    ],
    "exit": [
        "虧損達 8%：停損",
        "曾經賺 15% 以上，之後從最高點回落 7%：停利",
        "收盤跌破季線：趨勢轉弱出場",
        "法人評分跌破 55：籌碼轉弱出場",
        "持有滿 15 個交易日仍賺不到 3%：汰弱換股",
    ],
    "execution": [
        "收盤後決策，隔天開盤價成交；開盤漲停買不到、跌停賣不掉",
        "手續費 0.1425%（最低 20 元），賣出加收證交稅 0.3%",
        "可買零股，所以高價股也能照比例配置",
    ],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS arena_state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS arena_nav (
    date TEXT PRIMARY KEY, cash REAL, value REAL, nav REAL, bench REAL, phase TEXT, holdings INTEGER
);
CREATE TABLE IF NOT EXISTS arena_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT, name TEXT, side TEXT,
    shares INTEGER, price REAL, amount REAL, fee REAL, tax REAL, pnl REAL, pnl_pct REAL, reason TEXT
);
"""


def _fee(amount: float) -> float:
    return max(MIN_FEE, round(amount * FEE_RATE)) if amount > 0 else 0.0


# ---------------------------------------------------------------- 狀態存取
class Arena:
    def __init__(self, store):
        self.store = store
        self.conn = store._conn
        self.conn.executescript(SCHEMA)
        self._nav_rows: list[tuple] = []
        self._trade_rows: list[tuple] = []

    def _get(self) -> dict | None:
        row = self.conn.execute("SELECT v FROM arena_state WHERE k='state'").fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, state: dict) -> None:
        """狀態、每日淨值、成交紀錄一次寫入，中途關掉程式也不會重複記帳。"""
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO arena_nav VALUES(?,?,?,?,?,?,?)", self._nav_rows)
            self.conn.executemany("INSERT INTO arena_trades(date,code,name,side,shares,price,amount,fee,tax,pnl,"
                                  "pnl_pct,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", self._trade_rows)
            self.conn.execute("INSERT OR REPLACE INTO arena_state(k, v) VALUES('state', ?)",
                              (json.dumps(state, ensure_ascii=False),))
        self._nav_rows, self._trade_rows = [], []

    def reset(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM arena_state")
            self.conn.execute("DELETE FROM arena_nav")
            self.conn.execute("DELETE FROM arena_trades")

    # ------------------------------------------------------------ 主流程
    def pending(self) -> bool:
        """資料庫有新的交易日還沒算（或還沒開賽）。"""
        days = self.store.trading_days()
        if len(days) <= MIN_HISTORY:
            return False
        st = self._get()
        return st is None or st["last"] is None or days[-1] > st["last"]

    def run(self) -> dict:
        """補算到資料庫最新交易日。

        第一次開賽：從有法人資料的第一天開始回測到最新交易日，之後每個新交易日都算「實戰」。
        """
        days = self.store.trading_days()
        if len(days) <= MIN_HISTORY:
            return self._get() or {}
        state = self._get()
        if state is None:
            inst_days = sorted(set(self.store.dataset("inst")["date"]))
            first = inst_days[0] if inst_days else days[-1]
            first = max(first, days[MIN_HISTORY])   # 至少要有 60 天日 K 才算得出季線
            state = {"start": first, "live_after": days[-1], "last": None,
                     "cash": START_CASH, "positions": {}, "orders": [], "bench_base": None}
        todo = [d for d in days if d >= state["start"] and (state["last"] is None or d > state["last"])]
        if not todo:
            return state
        data = self._load()
        for d in todo:
            self._step(state, d, data)
            state["last"] = d
        self._save(state)
        return state

    def _load(self) -> dict:
        s = self.store
        bars = s.bars()
        bars["d"] = bars["date"].dt.strftime("%Y-%m-%d")
        stocks = s.stocks()
        return {
            "bars": bars, "names": dict(zip(stocks["code"], stocks["name"])),
            "markets": dict(zip(stocks["code"], stocks["market"])),
            "inst": s.dataset("inst"), "margin": s.dataset("margin"), "valuation": s.dataset("valuation"),
            "revenue": s.dataset("revenue"), "company": s.dataset("company"),
            "by_day": {d: g.set_index("code") for d, g in bars.groupby("d")},
        }

    # ------------------------------------------------------------ 單日
    def _step(self, st: dict, d: str, data: dict) -> None:
        today = data["by_day"].get(d)
        if today is None:
            return
        prev_days = [x for x in data["by_day"] if x < d]
        prev = data["by_day"][max(prev_days)] if prev_days else None

        # 1) 開盤執行昨天收盤後決定的委託：先賣後買
        orders, st["orders"] = st["orders"], []
        for o in [o for o in orders if o["side"] == "sell"]:
            self._sell(st, d, o, today, prev, data)
        for o in [o for o in orders if o["side"] == "buy"]:
            self._buy(st, d, o, today, prev, data)

        # 2) 收盤結算
        value = 0.0
        for code, p in st["positions"].items():
            if code in today.index and pd.notna(today.at[code, "close"]):
                p["last"] = float(today.at[code, "close"])
            p["peak"] = max(p.get("peak", p["last"]), p["last"])
            p["days"] = p.get("days", 0) + 1
            value += p["last"] * p["shares"]
        nav = st["cash"] + value
        bench = None
        if BENCHMARK in today.index and pd.notna(today.at[BENCHMARK, "close"]):
            bench_px = float(today.at[BENCHMARK, "close"])
            st["bench_base"] = st["bench_base"] or bench_px
            bench = START_CASH * bench_px / st["bench_base"]
        phase = "live" if d > st["live_after"] else "backtest"
        self._nav_rows.append((d, st["cash"], value, nav, bench, phase, len(st["positions"])))

        # 3) 收盤後決策（隔天開盤執行）
        df = self._alpha_asof(d, data)
        st["orders"] = self._decide(st, df, nav)
        st["nav"] = nav

    def _alpha_asof(self, d: str, data: dict) -> pd.DataFrame:
        bars = data["bars"][data["bars"]["d"] <= d]
        base = base_from_bars(bars, data["names"], d)
        inst = data["inst"][data["inst"]["date"] <= d]
        margin = data["margin"][data["margin"]["date"] <= d]
        val = data["valuation"]
        val = val[val["date"] <= d] if (val["date"] <= d).any() else val   # 回測早期只能用最早一份
        rev = data["revenue"]
        if not rev.empty:  # 月營收要等次月 10 日公布後才能用
            pub = rev["ym"].map(_published)
            rev = rev[pub <= d]
        return alpha.build(base, bars.drop(columns="d"), inst, margin, val, rev, data["company"], data["markets"])

    def _decide(self, st: dict, df: pd.DataFrame, nav: float) -> list[dict]:
        orders = []
        rows = df.set_index("code") if not df.empty else pd.DataFrame()
        selling = set()
        for code, p in st["positions"].items():
            r = rows.loc[code] if code in rows.index else None
            ret = p["last"] / p["avg"] - 1
            reason = None
            if ret <= -0.08:
                reason = f"停損（{ret * 100:.1f}%）"
            elif p["peak"] / p["avg"] - 1 >= 0.15 and p["last"] <= p["peak"] * 0.93:
                reason = f"停利：高點回落（{ret * 100:+.1f}%）"
            elif r is not None and pd.notna(r["ma60"]) and p["last"] < r["ma60"]:
                reason = "跌破季線"
            elif r is not None and r["score"] < 55:
                reason = f"評分轉弱（{r['score']:.0f} 分）"
            elif p["days"] >= 15 and ret < 0.03:
                reason = f"汰弱換股（{p['days']} 日 {ret * 100:+.1f}%）"
            if reason:
                orders.append({"side": "sell", "code": code, "name": p["name"], "reason": reason})
                selling.add(code)
        slots = MAX_POSITIONS - (len(st["positions"]) - len(selling))
        if slots > 0 and not rows.empty:
            c = rows[(rows["score"] >= 75) & (rows["s_chip"] >= 60) & (rows["vol20"] >= 1000)
                     & (rows["price"] >= rows["ma60"]) & (rows["chg"] < 7)
                     & (~rows.index.str.startswith("00"))   # ETF 不算，只比選股功力
                     & (~rows.index.isin(list(st["positions"])))].sort_values("score", ascending=False)
            budget = nav / MAX_POSITIONS
            for code, r in c.head(slots).iterrows():
                orders.append({"side": "buy", "code": code, "name": r["name"], "budget": budget,
                               "score": round(float(r["score"]), 1),
                               "reason": f"評分 {r['score']:.0f}" + (f"・{'、'.join(r['tags'][:3])}" if r["tags"] else "")})
        return orders

    # ------------------------------------------------------------ 成交
    @staticmethod
    def _open(today, prev, code):
        if code not in today.index:
            return None, None
        o = today.at[code, "open"]
        o = float(o) if pd.notna(o) else None
        pc = float(prev.at[code, "close"]) if prev is not None and code in prev.index and pd.notna(prev.at[code, "close"]) else None
        return o, pc

    def _sell(self, st, d, o, today, prev, data):
        p = st["positions"].get(o["code"])
        if not p:
            return
        px, pc = self._open(today, prev, o["code"])
        if px is None or (pc and px <= pc * 0.905):   # 停牌或開盤跌停：賣不掉，明天再評估
            return
        amount = px * p["shares"]
        fee, tax = _fee(amount), round(amount * TAX_RATE)
        proceeds = amount - fee - tax
        pnl = proceeds - p["cost"]
        st["cash"] += proceeds
        del st["positions"][o["code"]]
        self._trade(d, o["code"], p["name"], "sell", p["shares"], px, amount, fee, tax, pnl, pnl / p["cost"] * 100, o["reason"])

    def _buy(self, st, d, o, today, prev, data):
        px, pc = self._open(today, prev, o["code"])
        if px is None or px <= 0 or (pc and px >= pc * 1.095):   # 開盤漲停：買不到
            return
        budget = min(o["budget"], st["cash"])
        shares = int(budget / (px * (1 + FEE_RATE)))
        shares = shares // 1000 * 1000 if shares >= 1000 else shares   # 夠買整張就買整張
        if shares <= 0:
            return
        amount = px * shares
        fee = _fee(amount)
        if amount + fee > st["cash"]:
            return
        st["cash"] -= amount + fee
        st["positions"][o["code"]] = {"name": o["name"], "shares": shares, "avg": (amount + fee) / shares,
                                      "cost": amount + fee, "entry": d, "last": px, "peak": px, "days": 0}
        self._trade(d, o["code"], o["name"], "buy", shares, px, amount, fee, 0, None, None, o["reason"])

    def _trade(self, *row):
        self._trade_rows.append(row)

    # ------------------------------------------------------------ 成績
    def report(self) -> dict:
        st = self._get()
        nav = pd.read_sql_query("SELECT * FROM arena_nav ORDER BY date", self.conn)
        trades = pd.read_sql_query("SELECT * FROM arena_trades ORDER BY id DESC", self.conn)
        if st is None or nav.empty:
            return {"name": NAME, "rules": RULES, "start_cash": START_CASH, "ready": False}
        last = nav.iloc[-1]
        periods = []
        for label, n in (("1 週", 5), ("2 週", 10), ("3 週", 15), ("1 個月", 20), ("開賽至今", None)):
            if n is not None and len(nav) < n + 1:
                periods.append({"label": label, "days": n, "ret": None, "bench": None, "excess": None,
                                "grade": _grade(None, 0)})
                continue
            win = nav if n is None else nav.iloc[-n - 1:]
            b_nav = START_CASH if n is None else win.iloc[0]["nav"]
            b_bench = START_CASH if n is None else win.iloc[0]["bench"]
            ret = (last["nav"] / b_nav - 1) * 100
            bret = (last["bench"] / b_bench - 1) * 100 if pd.notna(last["bench"]) and pd.notna(b_bench) else None
            curve = pd.concat([pd.Series([b_nav]), win["nav"]]) if n is None else win["nav"]
            wmdd = float((curve / curve.cummax() - 1).min() * 100)
            ex = ret - bret if bret is not None else None
            periods.append({"label": label, "days": n or len(nav), "ret": ret, "bench": bret, "excess": ex,
                            "mdd": wmdd, "grade": _grade(ex, wmdd)})
        sells = trades[trades["side"] == "sell"]
        wins = int((sells["pnl"] > 0).sum())
        mdd = periods[-1]["mdd"]
        total = (last["nav"] / START_CASH - 1) * 100
        bench_total = (last["bench"] / START_CASH - 1) * 100 if pd.notna(last["bench"]) else None
        live = nav[nav["phase"] == "live"]
        live_ret = None
        if not live.empty:
            pre = nav[nav["date"] < live.iloc[0]["date"]]
            base_nav = pre.iloc[-1]["nav"] if not pre.empty else START_CASH
            live_ret = (last["nav"] / base_nav - 1) * 100
        excess = total - bench_total if bench_total is not None else None
        positions = []
        for code, p in st["positions"].items():
            positions.append({"code": code, "name": p["name"], "shares": p["shares"], "avg": p["avg"],
                              "last": p["last"], "value": p["last"] * p["shares"],
                              "pnl": p["last"] * p["shares"] - p["cost"], "pnl_pct": (p["last"] / p["avg"] - 1) * 100,
                              "entry": p["entry"], "days": p["days"],
                              "weight": p["last"] * p["shares"] / last["nav"] * 100})
        positions.sort(key=lambda x: -x["value"])
        return _clean({
            "name": NAME, "rules": RULES, "start_cash": START_CASH, "ready": True,
            "start": st["start"], "live_after": st["live_after"],
            "live_since": live.iloc[0]["date"] if not live.empty else None, "last": st["last"],
            "nav": last["nav"], "cash": st["cash"], "total_ret": total, "bench_total": bench_total,
            "excess": excess, "live_ret": live_ret, "mdd": mdd, "grade": _grade(excess, mdd),
            "trading_days": len(nav), "trades": int(len(trades)), "closed": int(len(sells)), "wins": wins,
            "win_rate": wins / len(sells) * 100 if len(sells) else None,
            "periods": periods,
            "curve": [{"date": r.date, "nav": r.nav, "bench": r.bench, "phase": r.phase} for r in nav.itertuples()],
            "positions": positions, "orders": st.get("orders", []),
            "recent_trades": json.loads(trades.head(60).to_json(orient="records", force_ascii=False)),
        })


def _clean(x):
    """numpy 數字轉成一般數字、NaN 轉成 None，才能輸出 JSON。"""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if math.isnan(x) or math.isinf(x) else float(x)
    return x


def _grade(excess: float | None, mdd: float) -> dict:
    """操盤評分 0～100：以「贏 0050 多少」為主，回檔太深扣分。"""
    if excess is None:
        return {"score": None, "letter": "—"}
    score = 60 + excess * 4 + min(0.0, mdd + 5) * 1.5
    score = max(0.0, min(100.0, score))
    letter = "S" if score >= 90 else "A" if score >= 75 else "B" if score >= 60 else "C" if score >= 45 else "D"
    return {"score": round(score), "letter": letter}


def _published(ym: str) -> str:
    """YYYY-MM 月營收的最晚公布日（次月 10 日）。"""
    y, m = int(ym[:4]), int(ym[5:7])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y:04d}-{m:02d}-10"


def base_from_bars(bars: pd.DataFrame, names: dict, d: str) -> pd.DataFrame:
    """快速版指標（只算評分需要的欄位），欄位名稱與 compute_metrics 相同。"""
    if bars.empty:
        return pd.DataFrame()
    b = bars.sort_values(["code", "date"])
    g = b.groupby("code")
    n = g.size()
    last = g.tail(1).set_index("code")
    prev = g.nth(-2).set_index("code")
    t20 = g.tail(20).groupby("code")
    t60 = g.tail(60).groupby("code")
    out = pd.DataFrame({
        "close": last["close"], "vol": last["volume"] / 1000, "last_d": last["d"],
        "prev": prev["close"],
        "vol20": t20["volume"].mean() / 1000, "ma60": t60["close"].mean(), "n": n,
    })
    out = out[(out["n"] >= alpha_min_bars()) & (out["last_d"] == d) & (out["prev"] > 0) & out["close"].notna()]
    out = out[out["vol"] > 0]
    return pd.DataFrame({
        '股票代號': out.index, '股票名稱': [names.get(c, "") for c in out.index], '現價(元)': out["close"].values,
        '今日漲跌(%)': ((out["close"] / out["prev"] - 1) * 100).round(2).values,
        '月均量(20日)': out["vol20"].round(0).values, '今日成交(張)': out["vol"].round(0).values,
        '月量爆發倍數': (out["vol"] / out["vol20"]).round(1).values, '季線位置': out["ma60"].round(2).values,
    })


def alpha_min_bars() -> int:
    from .indicators import MIN_BARS
    return MIN_BARS
