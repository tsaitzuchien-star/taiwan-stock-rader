"""法人級選股：籌碼、動能、成長、估值四因子評分（0～100）。

做法跟法人常用的多因子選股一樣：每個指標先在全市場排百分位，
再加權成因子分數與總分，避免大型股、小型股的絕對數字無法比較。
所有資料都來自本地資料庫（證交所、櫃買中心官方公開資料）。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEIGHTS = {"chip": 0.35, "momentum": 0.25, "growth": 0.25, "value": 0.15}
FACTOR_NAMES = {"chip": "籌碼", "momentum": "動能", "growth": "成長", "value": "估值"}
MIN_VOL20 = 300  # 張；流動性太差的股票法人不會碰

PRESETS = [
    {"id": "all", "name": "綜合評分", "desc": "四因子加權總分最高"},
    {"id": "trust", "name": "投信認養", "desc": "投信連續買超、買超佔成交量高"},
    {"id": "combo", "name": "土洋合擊", "desc": "外資與投信近 5 日同步買超"},
    {"id": "growth", "name": "營收爆發", "desc": "月營收年增 30% 以上且月增為正"},
    {"id": "value", "name": "低估成長", "desc": "本益比 15 倍以下、營收仍在成長"},
    {"id": "retreat", "name": "散戶退場", "desc": "法人買、融資減，籌碼往大戶集中"},
]


def _pct(s: pd.Series, higher_better: bool = True) -> pd.Series:
    """百分位分數 0～100；缺值給中性 50。"""
    r = s.rank(pct=True, ascending=higher_better) * 100
    return r.fillna(50.0)


def _streak(values: list[float]) -> int:
    """由最新一天往回算連續買超（正）或連續賣超（負）的天數。"""
    n, sign = 0, 0
    for v in reversed(values):
        if v is None or (isinstance(v, float) and math.isnan(v)) or v == 0:
            break
        s = 1 if v > 0 else -1
        if sign == 0:
            sign = s
        if s != sign:
            break
        n += 1
    return n * sign


def chip_table(inst: pd.DataFrame, margin: pd.DataFrame) -> pd.DataFrame:
    """每檔：外資 / 投信 / 自營 今日、5 日、20 日買賣超（張），連買天數，融資變化。"""
    out = pd.DataFrame()
    if not inst.empty:
        inst = inst.sort_values(["code", "date"])
        rows = []
        for code, g in inst.groupby("code", sort=False):
            f = (g["foreign_net"].fillna(0) / 1000).tolist()
            t = (g["trust_net"].fillna(0) / 1000).tolist()
            d = (g["dealer_net"].fillna(0) / 1000).tolist()
            rows.append({
                "code": code, "inst_date": g["date"].iloc[-1],
                "f1": f[-1], "t1": t[-1], "d1": d[-1],
                "f5": sum(f[-5:]), "t5": sum(t[-5:]), "d5": sum(d[-5:]),
                "f20": sum(f[-20:]), "t20": sum(t[-20:]),
                "f_streak": _streak(f), "t_streak": _streak(t),
            })
        out = pd.DataFrame(rows).set_index("code")
    if not margin.empty:
        margin = margin.sort_values(["code", "date"])
        m = margin.groupby("code").agg(
            margin_bal=("margin_bal", "last"), short_bal=("short_bal", "last"),
            margin_5ago=("margin_bal", lambda s: s.iloc[-6] if len(s) >= 6 else s.iloc[0]))
        m["margin_chg5"] = m["margin_bal"] - m["margin_5ago"]
        m["margin_chg5_pct"] = np.where(m["margin_5ago"] > 0, m["margin_chg5"] / m["margin_5ago"] * 100, np.nan)
        m["short_ratio"] = np.where(m["margin_bal"] > 0, m["short_bal"] / m["margin_bal"] * 100, np.nan)
        m = m.drop(columns="margin_5ago")
        out = m if out.empty else out.join(m, how="outer")
    return out


def returns_table(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame()
    rows = []
    for code, g in bars.groupby("code", sort=False):
        c = g["close"].dropna()
        if len(c) < 21:
            continue
        rows.append({"code": code, "ret20": (c.iloc[-1] / c.iloc[-21] - 1) * 100,
                     "ret60": (c.iloc[-1] / c.iloc[-61] - 1) * 100 if len(c) >= 61 else np.nan})
    return pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()


def build(metrics: pd.DataFrame, bars: pd.DataFrame, inst: pd.DataFrame, margin: pd.DataFrame,
          valuation: pd.DataFrame, revenue: pd.DataFrame, company: pd.DataFrame,
          markets: dict[str, str]) -> pd.DataFrame:
    """回傳全市場因子表（英文欄位），依總分排序。"""
    if metrics.empty:
        return pd.DataFrame()
    base = metrics.rename(columns={
        '股票代號': 'code', '股票名稱': 'name', '現價(元)': 'price', '今日漲跌(%)': 'chg',
        '月均量(20日)': 'vol20', '今日成交(張)': 'vol', '月量爆發倍數': 'vr', '季線位置': 'ma60'})
    base = base[["code", "name", "price", "chg", "vol20", "vol", "vr", "ma60"]].set_index("code")
    base["market"] = base.index.map(lambda c: markets.get(c, ""))
    base["dist_ma60"] = (base["price"] / base["ma60"] - 1) * 100

    df = base.join(returns_table(bars)).join(chip_table(inst, margin))
    if not valuation.empty:
        v = valuation.sort_values("date").groupby("code").last()[["pe", "pb", "dy"]]
        df = df.join(v)
    if not revenue.empty:
        r = revenue.sort_values("ym").groupby("code").last()[["ym", "revenue", "mom", "yoy", "cum_yoy"]]
        df = df.join(r.rename(columns={"ym": "rev_ym"}))
    if not company.empty:
        df = df.join(company.set_index("code")[["industry"]])
    for col in ["ret20", "ret60", "f1", "t1", "d1", "f5", "t5", "d5", "f20", "t20", "f_streak", "t_streak",
                "margin_bal", "short_bal", "margin_chg5", "margin_chg5_pct", "short_ratio",
                "pe", "pb", "dy", "rev_ym", "revenue", "mom", "yoy", "cum_yoy", "industry"]:
        if col not in df:
            df[col] = np.nan
    df["industry"] = df["industry"].fillna("其他")

    vol20 = df["vol20"].where(df["vol20"] > 0)
    df["legal5_ratio"] = (df["f5"] + df["t5"]) / (vol20 * 5) * 100     # 法人 5 日買超佔 5 日量 %
    df["trust5_ratio"] = df["t5"] / (vol20 * 5) * 100
    has_chip = df["f5"].notna()

    chip = (0.45 * _pct(df["legal5_ratio"]) + 0.35 * _pct(df["trust5_ratio"])
            + 0.20 * _pct(df["margin_chg5_pct"], higher_better=False))
    df["s_chip"] = chip.where(has_chip, 50.0)
    df["s_momentum"] = 0.4 * _pct(df["ret20"]) + 0.3 * _pct(df["ret60"]) + 0.3 * _pct(df["dist_ma60"])
    df["s_growth"] = (0.6 * _pct(df["yoy"]) + 0.4 * _pct(df["cum_yoy"])).where(df["yoy"].notna(), 50.0)
    pe_ok = df["pe"].where(df["pe"] > 0)
    pe_score = _pct(pe_ok, higher_better=False).where(pe_ok.notna(), 25.0)   # 虧損或沒有本益比：偏低分
    df["s_value"] = (0.6 * pe_score + 0.4 * _pct(df["dy"])).where(df["pe"].notna() | df["dy"].notna(), 50.0)
    df["score"] = sum(df[f"s_{k}"] * w for k, w in WEIGHTS.items())
    df["liquid"] = df["vol20"] >= MIN_VOL20
    df["tags"] = df.apply(_tags, axis=1)
    df = df.reset_index().sort_values("score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    return df


def _tags(r) -> list[str]:
    t = []
    if r["f1"] > 0 and r["t1"] > 0:
        t.append("土洋同買")
    if r["t_streak"] >= 3:
        t.append(f"投信連買{int(r['t_streak'])}")
    if r["f_streak"] >= 3:
        t.append(f"外資連買{int(r['f_streak'])}")
    if r["f_streak"] <= -3:
        t.append(f"外資連賣{int(-r['f_streak'])}")
    if r["yoy"] >= 30 and (r["mom"] or 0) > 0:
        t.append("營收爆發")
    if r["margin_chg5"] < 0 and (r["f5"] + r["t5"]) > 0:
        t.append("散戶退場")
    if 0 < r["pe"] <= 12:
        t.append("低本益比")
    if r["dy"] >= 5:
        t.append("高殖利率")
    return t


def apply_preset(df: pd.DataFrame, preset: str, min_vol20: float = MIN_VOL20) -> pd.DataFrame:
    if df.empty:
        return df
    d = df[df["vol20"] >= min_vol20]
    if preset == "trust":
        d = d[(d["t_streak"] >= 2) & (d["t5"] > 0)].sort_values("trust5_ratio", ascending=False)
    elif preset == "combo":
        d = d[(d["f5"] > 0) & (d["t5"] > 0)].sort_values("legal5_ratio", ascending=False)
    elif preset == "growth":
        d = d[(d["yoy"] >= 30) & (d["mom"] > 0)].sort_values("s_growth", ascending=False)
    elif preset == "value":
        d = d[(d["pe"] > 0) & (d["pe"] <= 15) & (d["yoy"] > 0)].sort_values("score", ascending=False)
    elif preset == "retreat":
        d = d[(d["margin_chg5"] < 0) & ((d["f5"] + d["t5"]) > 0)].sort_values("legal5_ratio", ascending=False)
    return d


def sectors(df: pd.DataFrame) -> list[dict]:
    """產業熱力圖：每個產業的平均漲跌、法人 5 日淨買超（億元）、平均分數。"""
    if df.empty:
        return []
    d = df.copy()
    d["legal5_amt"] = (d["f5"].fillna(0) + d["t5"].fillna(0)) * d["price"] * 1000 / 1e8
    d["turnover"] = d["vol"].fillna(0) * d["price"] * 1000 / 1e8
    g = d.groupby("industry").agg(n=("code", "size"), chg=("chg", "mean"), legal5=("legal5_amt", "sum"),
                                  score=("score", "mean"), turnover=("turnover", "sum"),
                                  up=("chg", lambda s: int((s > 0).sum())))
    g = g.sort_values("turnover", ascending=False).reset_index()
    return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items()}
            for row in g.to_dict(orient="records")]


def market_flows(inst: pd.DataFrame, bars: pd.DataFrame) -> list[dict]:
    """全市場三大法人每日買賣超金額（億元，以當日收盤價估算）。"""
    if inst.empty or bars.empty:
        return []
    b = bars[["code", "date", "close"]].copy()
    b["date"] = b["date"].dt.strftime("%Y-%m-%d")
    m = inst.merge(b, on=["code", "date"], how="inner")
    out = []
    for day, g in m.groupby("date"):
        out.append({"date": day,
                    **{k: round(float((g[col].fillna(0) * g["close"]).sum() / 1e8), 1)
                       for k, col in (("foreign", "foreign_net"), ("trust", "trust_net"), ("dealer", "dealer_net"))}})
    return out
