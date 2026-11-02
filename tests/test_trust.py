"""投信明日續買預測：特徵、機率表、走步回測、API。"""
import random

import pandas as pd
from fastapi.testclient import TestClient

from radar import trust as T
from radar.api.server import create_app
from radar.config import Settings
from radar.storage import Store

DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-06-01", periods=60)]
CODES = [f"{1100 + i}" for i in range(60)]


def _data(seed=1):
    rnd = random.Random(seed)
    bars, inst = [], []
    for c in CODES:
        st = 0
        for i, d in enumerate(DAYS):
            p = 50 + i * 0.1
            bars.append((c, d, p, p, p, p, 2_000_000))
            r = rnd.random()          # 投信有慣性：在買的很可能繼續買
            st = (1 if r < 0.8 else 0) if st == 1 else (1 if r < 0.1 else 0)
            inst.append((c, d, 0, st * 50_000, 0))
    return bars, inst


def _frames():
    bars, inst = _data()
    b = pd.DataFrame(bars, columns=["code", "date", "open", "high", "low", "close", "volume"])
    i = pd.DataFrame(inst, columns=["code", "date", "foreign_net", "trust_net", "dealer_net"])
    return i, b


def test_features_streak_and_label():
    inst, bars = _frames()
    f = T.features(inst, bars)
    one = f[f["code"] == CODES[0]].reset_index(drop=True)
    for k in range(1, len(one)):
        prev, cur = one["streak"][k - 1], one["streak"][k]
        assert cur == (prev + 1 if one["t"][k] > 0 and prev > 0 else 1 if one["t"][k] > 0 else 0)
    assert one["label"].iloc[-1] != one["label"].iloc[-1]          # 最後一天還沒有答案（NaN）
    assert (one["label"].iloc[:-1] == (one["t"].shift(-1).iloc[:-1] > 0)).all()


def test_predict_beats_base_rate():
    inst, bars = _frames()
    r = T.predict(inst, bars, {c: f"股{c}" for c in CODES})
    assert r["ready"] and r["asof"] == DAYS[-1] and len(r["rows"]) == 30
    assert r["rows"][0]["prob"] > 60 and r["rows"][0]["streak"] >= 1 and "隔天續買" in r["rows"][0]["why"]
    bt = r["backtest"]
    assert bt["days"] and bt["avg_hit"] > bt["avg_base"] * 2
    # 走步回測：每天的「答案日」都在預測日之後
    assert all(d["next"] > d["date"] for d in bt["days"])


def test_not_enough_history():
    inst, bars = _frames()
    r = T.predict(inst[inst["date"] <= DAYS[5]], bars)
    assert not r["ready"] and r["inst_days"] == 6 and r["need"] == T.MIN_TRAIN_DAYS + 1


def test_trust_api(tmp_path):
    bars, inst = _data()
    s = Store(tmp_path / "t.db")
    s.upsert_stocks([(c, f"股{c}", "TWSE") for c in CODES])
    s.upsert_bars(bars)
    s.upsert_dataset("inst", inst)
    s.close()
    c = TestClient(create_app(Settings(db_path=tmp_path / "t.db")))
    r = c.get("/api/trust/predict").json()
    assert r["ready"] and r["rows"][0]["name"].startswith("股")
