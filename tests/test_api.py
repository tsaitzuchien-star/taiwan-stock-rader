"""戰情室後端 API 測試：用假資料庫跑每個端點。"""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from radar.api.server import create_app
from radar.config import Settings
from radar.defense import evaluate
from radar.storage import Store
from radar.tactics import TACTIC_BY_ID, ScanParams, apply_filters


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "radar.db"
    store = Store(db)
    days = pd.bdate_range("2026-07-01", periods=70)
    store.upsert_stocks([("1101", "台泥", "TWSE"), ("6488", "環球晶", "TPEX"), ("9999", "停牌股", "TWSE")])
    rows = []
    for i, d in enumerate(days):
        ds = d.date().isoformat()
        rows.append(("1101", ds, 30.0, 30.5, 29.5, 30.0 + (i % 3) * 0.1, 400_000))
        rows.append(("6488", ds, 400.0, 404.0, 396.0, 400.0 - i * 0.5, 200_000))
        if i < 50:
            rows.append(("9999", ds, 10.0, 10.0, 10.0, 10.0, 1000))
    # 最後一天：1101 洗盤爆量長紅
    last = days[-1].date().isoformat()
    rows[-2] = ("1101", last, 28.6, 32.4, 28.5, 31.0, 3_000_000)
    store.upsert_bars(rows)
    store.close()
    app = create_app(Settings(db_path=db))
    return TestClient(app)


def test_health_and_stocks(client):
    h = client.get("/api/health").json()
    assert h["db"]["trading_days"] == 70 and h["db"]["stocks"] == 3
    assert "total_capital" not in h["settings"]
    codes = {s["code"]: s["market"] for s in client.get("/api/stocks").json()}
    assert codes == {"1101": "TWSE", "6488": "TPEX", "9999": "TWSE"}


def test_scan_tactic_and_custom(client):
    r = client.post("/api/scan", json={"tactic": "dive"}).json()
    assert r["universe"] == 2 and r["skipped"] == 1
    assert [x["code"] for x in r["rows"]] == ["1101"]
    row = r["rows"][0]
    assert row["washout"] is True and row["market"] == "TWSE" and len(row["spark"]) == 20
    r2 = client.post("/api/scan", json={"tactic": "dive", "params": {"p": 20}}).json()
    assert r2["rows"] == [] and r2["params"]["p"] == 20
    summary = {x["id"]: x["count"] for x in client.get("/api/tactics/summary").json()}
    assert summary["dive"] == 1


def test_breadth(client):
    b = client.get("/api/market/breadth").json()
    assert b["up"] == 1 and b["down"] == 1 and b["limit_up"] == 0


def test_stock_and_defense(client):
    s = client.get("/api/stock/6488").json()
    assert s["name"] == "環球晶" and len(s["candles"]) == 70 and s["candles"][-1]["volume"] == 200
    assert len(s["ma60"]) == 11 and s["metrics"]["code"] == "6488"
    assert client.get("/api/stock/0000").status_code == 404
    d = client.get("/api/stock/6488/defense", params={"cost": 450, "shares": 100}).json()
    assert d["roi_pct"] < -15 and d["verdict"]["level"] == "danger"
    assert "exposure" not in d and "max_buy_lots" not in s


def test_static_index(client):
    r = client.get("/")
    assert r.status_code == 200 and "台股雷達" in r.text


def test_apply_filters_matches_tactics():
    assert apply_filters(pd.DataFrame(), ScanParams()).empty
    assert TACTIC_BY_ID["blitz"]["params"].sqz and TACTIC_BY_ID["blitz"]["params"].gap


def test_defense_verdicts():
    up = pd.Series([100.0] * 59 + [115.0])
    ok = evaluate(80, 1000, up)
    assert ok["verdict"]["level"] == "ok" and ok["unrealized"] > 0
    hot = evaluate(80, 1000, up, heat=12)
    assert hot["verdict"]["level"] == "take"
    weak = evaluate(100, 1000, pd.Series([100.0] * 59 + [97.0]))
    assert weak["verdict"]["level"] == "warn" and weak["escape_weak"] is not None
