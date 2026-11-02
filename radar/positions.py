"""持倉風控：元大庫存 + 即時報價 + 本地日 K，算出即時損益、季線防線與警示。

這裡只做計算（不碰元大元件），方便測試；資料由 api/server.py 的 YuantaHub 提供。
"""
from __future__ import annotations

import math

import pandas as pd

from .defense import evaluate

SELL_COST = 0.001425 + 0.003   # 賣出手續費 + 證交稅（估算即時損益用）
NEAR_MA60_PCT = 2.0            # 距離季線 2% 以內：逼近季線
DROP_ALERT_PCT = -5.0          # 今日跌幅超過 5%：重挫
LOSS_ALERT_PCT = -8.0          # 帳面虧損超過 8%：停損警戒


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def build(holdings: pd.DataFrame, quotes: dict[str, dict], closes: dict[str, pd.Series],
          today: str) -> dict:
    """holdings：元大 parse_store_summary 的表；quotes：{代號: 即時報價}；closes：{代號: 依日期排序的收盤價}。"""
    rows, alerts = [], []
    if holdings is None or holdings.empty:
        return {"rows": [], "alerts": [], "totals": _totals([])}
    for h in holdings.to_dict(orient="records"):
        code = str(h["股票代號"])
        q = quotes.get(code, {})
        shares = int(h["股數"] or 0)
        cost = _f(h["持有成本"]) or 0.0
        avg = _f(h["成交均價"]) or (cost / shares if shares else 0.0)
        y_price = _f(h["市價"])
        price = _f(q.get("price")) or y_price
        hist = closes.get(code, pd.Series(dtype=float)).dropna()
        prev = _f(q.get("prev_close")) or (float(hist.iloc[-1]) if len(hist) else None)
        if price is None:
            price = prev
        y_pnl = _f(h["未實現損益"])
        if y_pnl is not None and y_price:
            pnl = y_pnl + (price - y_price) * shares * (1 - SELL_COST)   # 以元大的損益為準，再加上之後的價格變動
        elif price is not None:
            pnl = price * shares * (1 - SELL_COST) - cost
        else:
            pnl = None
        value = price * shares if price is not None else _f(h["股票市值"])
        row = {
            "code": code, "name": h["股票名稱"] or q.get("name", ""), "market": h["市場"], "kind": h["交易種類"],
            "shares": shares, "sellable": int(h["可交易股數"] or 0), "avg": avg, "cost": cost,
            "price": price, "prev_close": prev, "value": value, "pnl": pnl,
            "pnl_pct": pnl / cost * 100 if pnl is not None and cost else None,
            "day_chg_pct": (price / prev - 1) * 100 if price and prev else None,
            "day_pnl": (price - prev) * shares if price and prev else None,
            "quote_time": q.get("time", ""), "live": bool(q.get("price")),
            "ma20": None, "ma60": None, "dist_ma60": None, "limit_down_pl": None, "verdict": None, "escape": None,
        }
        if row["kind"] != "融券" and len(hist) >= 20 and avg > 0 and shares > 0 and price:
            series = hist.copy()
            if str(series.index[-1])[:10] < today:   # 盤中：把即時價當成今天的收盤
                series = pd.concat([series, pd.Series([price], index=[today])])
            else:
                series.iloc[-1] = price
            d = evaluate(avg, shares, series.reset_index(drop=True))
            row.update({"ma20": d["ma20"], "ma60": d["ma60"], "dist_ma60": d["bias_ma60_pct"],
                        "limit_down_pl": d["limit_down_pl"], "verdict": d["verdict"],
                        "escape": d["escape_weak"]})
        rows.append(row)
        alerts.extend(_alerts(row))
    rows.sort(key=lambda r: -(r["value"] or 0))
    order = {"danger": 0, "take": 1, "warn": 2}
    alerts.sort(key=lambda a: order.get(a["level"], 9))
    return {"rows": rows, "alerts": alerts, "totals": _totals(rows)}


def _alerts(r: dict) -> list[dict]:
    out = []
    name = f"{r['code']} {r['name']}"
    add = lambda level, title, text: out.append({"code": r["code"], "name": r["name"], "level": level,  # noqa: E731
                                                  "title": title, "text": text})
    if r["ma60"]:
        if r["price"] < r["ma60"]:
            add("danger", "跌破季線", f"{name} 現價 {r['price']:g} 低於季線 {r['ma60']:.2f}，趨勢轉弱。")
        elif r["dist_ma60"] is not None and r["dist_ma60"] < NEAR_MA60_PCT:
            add("warn", "逼近季線", f"{name} 距離季線只剩 {r['dist_ma60']:.1f}%，季線 {r['ma60']:.2f} 是防守線。")
    if r["day_chg_pct"] is not None and r["day_chg_pct"] <= DROP_ALERT_PCT:
        add("warn", "今日重挫", f"{name} 今天跌 {r['day_chg_pct']:.1f}%。")
    if r["pnl_pct"] is not None and r["pnl_pct"] <= LOSS_ALERT_PCT:
        add("danger", "虧損擴大", f"{name} 帳面虧損 {r['pnl_pct']:.1f}%，已超過 8% 停損警戒。")
    v = r.get("verdict")
    if v and v["level"] == "take":
        add("take", v["title"], f"{name}：{v['text']}")
    return out


def _totals(rows: list[dict]) -> dict:
    value = sum(r["value"] or 0 for r in rows)
    cost = sum(r["cost"] or 0 for r in rows)
    pnl = sum(r["pnl"] or 0 for r in rows)
    day = sum(r["day_pnl"] or 0 for r in rows)
    prev_value = value - day
    return {"count": len(rows), "value": value, "cost": cost, "pnl": pnl,
            "pnl_pct": pnl / cost * 100 if cost else None, "day_pnl": day,
            "day_pct": day / prev_value * 100 if prev_value else None}


def realized_summary(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"rows": [], "total": 0.0, "count": 0, "wins": 0}
    d = df.rename(columns={"成交日": "date", "股票代號": "code", "市場": "market", "交易種類": "kind",
                           "成交價": "price", "股數": "shares", "已實現損益": "pnl", "手續費": "fee",
                           "交易稅": "tax", "成本": "cost"})
    d = d.sort_values("date", ascending=False)
    rows = [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}
            for r in d.to_dict(orient="records")]
    pnl = d["pnl"].fillna(0)
    return {"rows": rows, "total": float(pnl.sum()), "count": int(len(d)), "wins": int((pnl > 0).sum())}
