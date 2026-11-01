"""百萬操盤擂台：模擬成交、增量補算、評分與 API。"""
import random
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from radar import arena as A
from radar.api.server import create_app
from radar.config import Settings
from radar.storage import Store

DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-07-01", periods=80)]
STARS = ("2330", "2454")   # 法人狂買、股價穩定走高
CODES = [f"{1101 + i}" for i in range(10)] + list(STARS)


def _market():
    rnd = random.Random(7)
    bars, inst, margin = [], [], []
    for c in CODES + ["0050"]:
        p = 100.0
        star = c in STARS
        for i, d in enumerate(DAYS):
            o = p
            p = p * (1.006 if star else 1 + rnd.gauss(0, 0.012))
            bars.append((c, d, round(o, 2), round(max(o, p) * 1.01, 2), round(min(o, p) * 0.99, 2),
                         round(p, 2), 3_000_000 if star else 1_500_000))
            if i >= 60 and c != "0050":
                inst.append((c, d, 600_000 if star else rnd.randint(-100_000, 100_000),
                             200_000 if star else rnd.randint(-20_000, 20_000), 0))
                margin.append((c, d, 5000 - (i * 20 if star else 0), 100))
    val = [(c, DAYS[-1], 10.0 if c in STARS else 25.0, 2.0, 5.0 if c in STARS else 2.0) for c in CODES]
    rev = [(c, "2026-08", 1e6, 5.0, 60.0 if c in STARS else 0.0, 40.0 if c in STARS else 0.0) for c in CODES]
    return bars, inst, margin, val, rev


def _db(path, upto=None):
    bars, inst, margin, val, rev = _market()
    keep = set(DAYS[:upto]) if upto else set(DAYS)
    s = Store(path)
    s.upsert_stocks([(c, f"股{c}", "TWSE") for c in CODES] + [("0050", "元大台灣50", "TWSE")])
    s.upsert_bars([r for r in bars if r[1] in keep])
    s.upsert_dataset("inst", [r for r in inst if r[1] in keep])
    s.upsert_dataset("margin", [r for r in margin if r[1] in keep])
    s.upsert_dataset("valuation", val)
    s.upsert_dataset("revenue", rev)
    s.upsert_dataset("company", [(c, "半導體業") for c in CODES])
    return s


def test_run_trades_and_books_balance(tmp_path):
    s = _db(tmp_path / "a.db")
    a = A.Arena(s)
    assert a.pending()
    st = a.run()
    assert not a.pending()
    assert st["start"] == DAYS[60] and st["last"] == DAYS[-1] and st["live_after"] == DAYS[-1]
    r = a.report()
    assert r["ready"] and r["trading_days"] == 20
    assert r["curve"][0]["nav"] == A.START_CASH and r["curve"][0]["phase"] == "backtest"
    bought = {t["code"] for t in r["recent_trades"] if t["side"] == "buy"}
    assert set(STARS) <= bought and "0050" not in bought
    # 現金 + 持股市值 = 總資產；買進有扣手續費
    assert r["cash"] + sum(p["value"] for p in r["positions"]) == pytest.approx(r["nav"])
    assert all(t["fee"] >= A.MIN_FEE for t in r["recent_trades"])
    assert r["bench_total"] is not None and r["grade"]["letter"] in "SABCD"
    assert [p["label"] for p in r["periods"]] == ["1 週", "2 週", "3 週", "1 個月", "開賽至今"]
    assert r["periods"][0]["ret"] is not None and r["periods"][3]["ret"] is None   # 只有 20 天，還沒滿 1 個月


def test_incremental_equals_full(tmp_path):
    full = A.Arena(_db(tmp_path / "full.db"))
    full.run()
    s = _db(tmp_path / "inc.db", upto=72)
    inc = A.Arena(s)
    inc.run()
    bars, inst, margin, _, _ = _market()
    s.upsert_bars([r for r in bars if r[1] in DAYS[72:]])
    s.upsert_dataset("inst", [r for r in inst if r[1] in DAYS[72:]])
    s.upsert_dataset("margin", [r for r in margin if r[1] in DAYS[72:]])
    assert inc.pending()
    inc.run()
    a, b = full.report(), inc.report()
    assert b["nav"] == pytest.approx(a["nav"]) and b["trades"] == a["trades"]
    # 第二次之後算出來的日子才算「實戰」
    assert b["live_since"] == DAYS[72] and a["live_since"] is None
    assert b["live_ret"] is not None


def test_limit_up_open_cannot_buy_and_limit_down_cannot_sell(tmp_path):
    a = A.Arena(Store(tmp_path / "x.db"))
    prev = pd.DataFrame({"close": [100.0]}, index=["2330"])
    today = pd.DataFrame({"open": [110.0], "close": [110.0]}, index=["2330"])
    st = {"cash": A.START_CASH, "positions": {}}
    a._buy(st, "d", {"code": "2330", "name": "x", "budget": 125_000, "reason": ""}, today, prev, {})
    assert st["positions"] == {}
    today.loc["2330", "open"] = 101.0
    a._buy(st, "d", {"code": "2330", "name": "x", "budget": 125_000, "reason": ""}, today, prev, {})
    p = st["positions"]["2330"]
    assert p["shares"] == 1000 and st["cash"] == pytest.approx(A.START_CASH - 101_000 - 144)
    today.loc["2330", "open"] = 90.0
    a._sell(st, "d", {"code": "2330", "reason": ""}, today, prev, {})
    assert "2330" in st["positions"]   # 跌停賣不掉


def test_grade_and_publish_date():
    assert A._grade(None, 0)["letter"] == "—"
    assert A._grade(0, -3) == {"score": 60, "letter": "B"}
    assert A._grade(10, -2)["letter"] == "S"
    assert A._grade(-5, -15)["score"] == 25
    assert A._published("2026-09") == "2026-10-10" and A._published("2026-12") == "2027-01-10"


def test_api_arena(tmp_path):
    _db(tmp_path / "r.db").close()
    client = TestClient(create_app(Settings(db_path=tmp_path / "r.db")))
    r = client.get("/api/arena").json()
    for _ in range(100):
        if not r["running"] and r["ready"]:
            break
        time.sleep(0.1)
        r = client.get("/api/arena").json()
    assert r["ready"] and r["name"] == A.NAME and r["error"] is None
    assert len(r["curve"]) == 20
    r2 = client.post("/api/arena/reset").json()
    assert r2["running"] or r2["ready"]
