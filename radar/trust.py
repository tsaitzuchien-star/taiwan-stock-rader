"""投信明日續買預測。

做法（純統計，不是黑盒子）：
1. 每檔每天整理三個特徵：
   - 投信連買／連賣天數（投信建立部位通常分好幾天買，這是最強的訊號）
   - 今天投信買超佔 20 日均量的比例（越大代表越在意這檔）
   - 收盤是否站上月線（投信偏好趨勢向上的股票）
2. 用資料庫裡的歷史，數出「同樣情況下，隔天投信真的繼續買超」的比例，就是機率。
   樣本少的組合會往上一層（只看連買天數）收斂，避免小樣本亂跳。
3. 回測用「走步法」：預測某一天時只用那天以前的資料，所以命中率不會作弊。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_VOL20 = 300_000      # 股；20 日均量 300 張以下太冷門，投信很少碰
TOP_N = 20
SMOOTH = 20              # 小樣本往上一層收斂的強度
MIN_TRAIN_DAYS = 15
BACKTEST_DAYS = 40

STREAK_LABEL = {"sell": "投信賣超中", "0": "今天沒買", "1": "今天剛買（第 1 天）", "2": "連買 2 天",
                "3-4": "連買 3～4 天", "5+": "連買 5 天以上"}
INTENSITY_LABEL = {"none": "沒買", "s": "買超佔量 0.5% 以下", "m": "買超佔量 0.5～2%", "l": "買超佔量 2% 以上"}


def _streak_bucket(s: pd.Series) -> pd.Series:
    return pd.Series(np.select([s < 0, s == 0, s == 1, s == 2, s <= 4], ["sell", "0", "1", "2", "3-4"], "5+"),
                     index=s.index)


def _intensity_bucket(x: pd.Series) -> pd.Series:
    return pd.Series(np.select([x <= 0, x <= 0.5, x <= 2], ["none", "s", "m"], "l"), index=x.index)


def features(inst: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """每檔每天一列：投信買賣超、連買天數、佔量比例、是否站上月線，以及隔天是否續買（答案）。"""
    if inst.empty or bars.empty:
        return pd.DataFrame()
    d = inst[["code", "date", "trust_net"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["t"] = d["trust_net"].fillna(0.0)
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    sign = np.sign(d["t"])
    new_run = (sign != sign.shift()) | (d["code"] != d["code"].shift())
    run = new_run.cumsum()
    d["streak"] = (d.groupby(run).cumcount() + 1) * sign

    b = bars[["code", "date", "close", "volume"]].copy()
    b["date"] = pd.to_datetime(b["date"])
    b = b.sort_values(["code", "date"])
    g = b.groupby("code", sort=False)
    b["vol20"] = g["volume"].transform(lambda v: v.rolling(20, min_periods=10).mean())
    b["ma20"] = g["close"].transform(lambda v: v.rolling(20, min_periods=10).mean())
    d = d.merge(b[["code", "date", "close", "vol20", "ma20"]], on=["code", "date"], how="left")

    d["intensity"] = np.where(d["vol20"] > 0, d["t"] / d["vol20"] * 100, 0.0)
    d["trend"] = np.where(d["close"] >= d["ma20"], "up", "down")
    d["sb"] = _streak_bucket(d["streak"])
    d["ib"] = _intensity_bucket(d["intensity"])
    nxt = d.groupby("code", sort=False)["t"].shift(-1)
    nxt_date = d.groupby("code", sort=False)["date"].shift(-1)
    d["label"] = np.where(nxt.isna(), np.nan, (nxt > 0).astype(float))
    d["next_date"] = nxt_date
    d["liquid"] = (d["vol20"] >= MIN_VOL20) & ~d["code"].str.startswith("00")
    return d


class Model:
    """分層機率表：(連買天數) → (連買天數, 佔量) → (連買天數, 佔量, 月線)。"""

    def __init__(self, train: pd.DataFrame):
        t = train[train["label"].notna() & train["liquid"]]
        self.n = len(t)
        self.p0 = float(t["label"].mean()) if self.n else 0.0
        self.l1 = t.groupby("sb")["label"].agg(["sum", "count"])
        self.l2 = t.groupby(["sb", "ib"])["label"].agg(["sum", "count"])
        self.l3 = t.groupby(["sb", "ib", "trend"])["label"].agg(["sum", "count"])

    @staticmethod
    def _shrink(tab: pd.DataFrame, keys: pd.MultiIndex | pd.Index, prior: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        r = tab.reindex(keys)
        k = r["sum"].fillna(0).to_numpy()
        n = r["count"].fillna(0).to_numpy()
        return (k + SMOOTH * prior) / (n + SMOOTH), n

    def predict(self, x: pd.DataFrame) -> pd.DataFrame:
        prior = np.full(len(x), self.p0)
        p1, _ = self._shrink(self.l1, pd.Index(x["sb"]), prior)
        p2, _ = self._shrink(self.l2, pd.MultiIndex.from_arrays([x["sb"], x["ib"]]), p1)
        p3, n3 = self._shrink(self.l3, pd.MultiIndex.from_arrays([x["sb"], x["ib"], x["trend"]]), p2)
        out = x.copy()
        out["prob"] = p3 * 100
        out["samples"] = n3.astype(int)
        return out


def _rank(pred: pd.DataFrame) -> pd.DataFrame:
    return pred.sort_values(["prob", "intensity"], ascending=False)


def backtest(feat: pd.DataFrame, days: int = BACKTEST_DAYS) -> dict:
    """走步回測：每一天只用之前的資料建模，挑前 20 名，看隔天投信是不是真的買超。"""
    dates = sorted(feat["date"].unique())
    out = []
    start = max(MIN_TRAIN_DAYS, len(dates) - days - 1)
    for day in dates[start:]:
        today = feat[(feat["date"] == day) & feat["liquid"] & feat["label"].notna()]
        if today.empty:
            continue
        model = Model(feat[(feat["date"] < day) & (feat["next_date"] <= day)])
        top = _rank(model.predict(today)).head(TOP_N)
        out.append({"date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                    "next": pd.Timestamp(top["next_date"].iloc[0]).strftime("%Y-%m-%d"),
                    "hit": round(float(top["label"].mean()) * 100, 1), "hits": int(top["label"].sum()),
                    "n": len(top), "base": round(float(today["label"].mean()) * 100, 1)})
    if not out:
        return {"days": [], "avg_hit": None, "avg_base": None, "lift": None}
    avg_hit = sum(x["hit"] for x in out) / len(out)
    avg_base = sum(x["base"] for x in out) / len(out)
    return {"days": out, "avg_hit": round(avg_hit, 1), "avg_base": round(avg_base, 1),
            "lift": round(avg_hit / avg_base, 1) if avg_base else None}


def _why(r) -> str:
    s = int(r["streak"])
    head = ("投信今天開始買" if s == 1 else f"投信已連買 {s} 天" if s > 1
            else f"投信連賣 {-s} 天" if s < 0 else "投信今天沒有買賣")
    inten = f"今天買超佔 20 日均量 {r['intensity']:.1f}%" if r["t"] > 0 else ""
    trend = "站上月線" if r["trend"] == "up" else "在月線下"
    parts = "、".join(x for x in (head, inten, trend) if x)
    return f"{parts}；歷史上同樣情況出現 {r['samples']:,} 次，隔天續買 {r['prob']:.0f}%"


def predict(inst: pd.DataFrame, bars: pd.DataFrame, names: dict[str, str] | None = None) -> dict:
    feat = features(inst, bars)
    if feat.empty or feat["date"].nunique() < MIN_TRAIN_DAYS + 1:
        have = 0 if feat.empty else int(feat["date"].nunique())
        return {"ready": False, "inst_days": have, "need": MIN_TRAIN_DAYS + 1, "rows": [], "backtest": backtest(feat) if have else None}
    last = feat["date"].max()
    model = Model(feat[feat["label"].notna()])
    today = feat[(feat["date"] == last) & feat["liquid"]]
    top = _rank(model.predict(today)).head(30)
    t5 = (feat[feat["date"] > sorted(feat["date"].unique())[-6]].groupby("code")["t"].sum() / 1000)
    names = names or {}
    rows = [{"code": r["code"], "name": names.get(r["code"], ""), "prob": round(float(r["prob"]), 1),
             "samples": int(r["samples"]), "streak": int(r["streak"]), "t1": round(r["t"] / 1000),
             "t5": round(float(t5.get(r["code"], 0))), "intensity": round(float(r["intensity"]), 2),
             "trend": r["trend"], "close": None if pd.isna(r["close"]) else float(r["close"]),
             "why": _why(r)} for _, r in top.iterrows()]
    return {"ready": True, "asof": pd.Timestamp(last).strftime("%Y-%m-%d"), "rows": rows,
            "base": round(model.p0 * 100, 1), "train_rows": model.n,
            "inst_days": int(feat["date"].nunique()), "backtest": backtest(feat)}
