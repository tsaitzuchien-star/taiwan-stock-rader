"""持倉風控：即時損益、季線警示，以及元大相關 API（用假的元大連線）。"""
import threading
from collections import deque

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from radar import positions as P
from radar.api.server import create_app
from radar.config import Settings
from radar.storage import Store
from radar.yuanta.parsers import HOLDING_COLUMNS

DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-07-01", periods=70)]


def _holdings():
    return pd.DataFrame([
        # 代號, 名稱, 市場, 種類, 股數, 可交易, 均價, 成本, 市價, 市值, 未實現, 報酬率
        ["2330", "台積電", "TWSE", "現股", 1000, 1000, 1000.0, 1_001_425.0, 1100.0, 1_100_000.0, 93_783.0, 9.36],
        ["6488", "環球晶", "TPEX", "現股", 2000, 2000, 500.0, 1_001_425.0, 400.0, 800_000.0, -205_000.0, -20.5],
    ], columns=HOLDING_COLUMNS)


def _closes():
    up = pd.Series([900 + i * 3.0 for i in range(70)], index=DAYS)      # 一路走高，站在季線上
    down = pd.Series([600 - i * 2.0 for i in range(70)], index=DAYS)    # 一路走低
    return {"2330": up, "6488": down}


def test_live_pnl_and_alerts():
    quotes = {"2330": {"price": 1110.0, "prev_close": 1100.0, "time": "13:20:00"},
              "6488": {"price": 380.0, "prev_close": 400.0, "time": "13:20:00"}}
    out = P.build(_holdings(), quotes, _closes(), "2026-11-02")
    r = {x["code"]: x for x in out["rows"]}
    # 以元大的損益為準，再加上之後的價格變動（扣賣出成本）
    assert r["2330"]["pnl"] == pytest.approx(93_783 + 10 * 1000 * (1 - P.SELL_COST))
    assert r["2330"]["day_pnl"] == pytest.approx(10_000) and r["2330"]["live"]
    assert r["6488"]["day_chg_pct"] == pytest.approx(-5.0)
    assert r["2330"]["ma60"] is not None and r["2330"]["verdict"]["level"] in ("ok", "take", "warn")
    titles = {(a["code"], a["title"]) for a in out["alerts"]}
    assert ("6488", "跌破季線") in titles and ("6488", "今日重挫") in titles and ("6488", "虧損擴大") in titles
    assert not any(c == "2330" and t in ("跌破季線", "虧損擴大") for c, t in titles)
    assert out["alerts"][0]["level"] == "danger"
    t = out["totals"]
    assert t["count"] == 2 and t["value"] == pytest.approx(1110 * 1000 + 380 * 2000)
    assert t["day_pnl"] == pytest.approx(10_000 - 40_000)


def test_without_quotes_uses_yuanta_price():
    out = P.build(_holdings(), {}, _closes(), "2026-11-02")
    r = {x["code"]: x for x in out["rows"]}
    assert r["2330"]["price"] == 1100.0 and r["2330"]["pnl"] == pytest.approx(93_783)
    assert not r["2330"]["live"]
    assert P.build(pd.DataFrame(columns=HOLDING_COLUMNS), {}, {}, "2026-11-02")["rows"] == []


class FakeClient:
    def __init__(self, cfg):
        self.logged_in = False
        self.subscribed, self.quotes, self.messages = set(), {}, deque()
        self._lock = threading.Lock()
        self.calls = []

    def connect(self):
        self.calls.append("connect")
        self.logged_in = True
        return {"ok": True}

    def holdings(self):
        self.calls.append("holdings")
        return _holdings()

    def snapshot(self, pairs):
        self.calls.append(("snapshot", tuple(c for c, _ in pairs)))
        for c, m in pairs:
            self.quotes[c] = {"price": 1110.0 if c == "2330" else 380.0, "prev_close": 1100.0 if c == "2330" else 400.0}

    def subscribe(self, pairs):
        self.subscribed.update(c for c, _ in pairs)

    def realized(self, start, end):
        return pd.DataFrame([{"成交日": "2026/10/30", "股票代號": "2317", "市場": "TWSE", "交易種類": "現股",
                              "成交價": 200.0, "股數": 1000, "已實現損益": 12000.0, "手續費": 285.0,
                              "交易稅": 600.0, "成本": 187000.0}])

    def close(self):
        self.logged_in = False


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "r.db"
    s = Store(db)
    s.upsert_stocks([("2330", "台積電", "TWSE"), ("6488", "環球晶", "TPEX")])
    c = _closes()
    s.upsert_bars([(code, d, v, v, v, v, 1_000_000) for code, ser in c.items() for d, v in ser.items()])
    s.close()
    app = create_app(Settings(db_path=db, yuanta={"account": "S98875005091", "password": "x", "sdk_dir": "C:/x"}))
    app.state.engine.yuanta._factory = FakeClient
    return TestClient(app)


def test_yuanta_api_flow(client):
    st = client.get("/api/yuanta/status").json()
    assert st["configured"] and not st["logged_in"] and st["account"] == "S9887*******"
    assert client.get("/api/positions").status_code == 409
    assert client.post("/api/yuanta/connect").json()["logged_in"]
    p = client.get("/api/positions").json()
    assert [r["code"] for r in p["rows"]] == ["2330", "6488"] and p["rows"][0]["live"]
    assert any(a["title"] == "跌破季線" for a in p["alerts"])
    fake = client.app.state.engine.yuanta.client
    client.get("/api/positions")
    assert fake.calls.count("holdings") == 1        # 20 秒內不重查庫存
    client.get("/api/positions?refresh=true")
    assert fake.calls.count("holdings") == 2
    assert sum(1 for c in fake.calls if isinstance(c, tuple)) == 1   # 已訂閱的不重覆訂閱
    r = client.get("/api/positions/realized?days=30").json()
    assert r["total"] == 12000 and r["wins"] == 1 and r["rows"][0]["code"] == "2317"
    assert not client.post("/api/yuanta/disconnect").json()["logged_in"]


def test_not_configured(tmp_path):
    Store(tmp_path / "e.db").close()
    c = TestClient(create_app(Settings(db_path=tmp_path / "e.db")))
    assert not c.get("/api/yuanta/status").json()["configured"]
    r = c.post("/api/yuanta/connect")
    assert r.status_code == 502 and "config.local.toml" in r.json()["detail"]


def test_live_breadth_from_yuanta(client, monkeypatch):
    import time as _t
    from datetime import datetime as real_dt

    import radar.api.server as srv

    class FakeNow(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 11, 2, 10, 15)   # 週一盤中，資料庫還沒有今天
    monkeypatch.setattr(srv, "datetime", FakeNow)
    assert client.get("/api/market/breadth").json()["live"] is False   # 還沒連元大：用盤後資料
    client.post("/api/yuanta/connect")
    b = client.get("/api/market/breadth").json()
    for _ in range(50):
        if b["live"]:
            break
        _t.sleep(0.05)
        b = client.get("/api/market/breadth").json()
    assert b["live"] and b["latest"] == "2026-11-02" and not b["closed"]
    assert b["up"] == 1 and b["down"] == 1 and b["count"] == 2   # 2330 漲、6488 跌
    assert b["above_ma60_pct"] == 50.0
