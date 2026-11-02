"""觀察名單：記錄加入當天的價格與分數，之後算 3 天 / 1 週 / 2 週報酬並統計準確度。"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from radar import watch as W
from radar.api.server import create_app
from radar.config import Settings
from radar.storage import Store

DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-07-01", periods=80)]


def _bars():
    rows = []
    for i, d in enumerate(DAYS):
        up = 100 * 1.01 ** i          # 每天漲 1%
        dn = 100 * 0.99 ** i          # 每天跌 1%
        bench = 100 + i * 0.1
        rows += [("2330", d, up, up * 1.02, up * 0.99, up, 5_000_000),
                 ("6488", d, dn, dn * 1.01, dn * 0.98, dn, 5_000_000),
                 ("0050", d, bench, bench, bench, bench, 5_000_000)]
    return rows


def _store(tmp_path):
    s = Store(tmp_path / "w.db")
    s.upsert_stocks([("2330", "台積電", "TWSE"), ("6488", "環球晶", "TPEX"), ("0050", "元大台灣50", "TWSE")])
    s.upsert_bars(_bars())
    return s


def test_tracks_horizons_and_stats(tmp_path):
    s = _store(tmp_path)
    w = W.Watch(s)
    i0 = 60
    p_up, p_dn = 100 * 1.01 ** i0, 100 * 0.99 ** i0
    w.add({"code": "2330", "name": "台積電", "added_date": DAYS[i0], "price": p_up, "entry": 86, "source": "老牛翻身"})
    w.add({"code": "6488", "name": "環球晶", "added_date": DAYS[i0], "price": p_dn, "entry": 40, "source": "老牛翻身"})
    # 同一天同戰術重複加入只會更新，不會多一筆
    w.add({"code": "2330", "name": "台積電", "added_date": DAYS[i0], "price": p_up, "entry": 88, "source": "老牛翻身"})
    rep = w.report(s.bars())
    r = {x["code"]: x for x in rep["rows"]}
    assert len(rep["rows"]) == 2 and r["2330"]["entry"] == 88
    assert r["2330"]["d3"] == pytest.approx((1.01 ** 3 - 1) * 100, abs=0.01)
    assert r["2330"]["d10"] == pytest.approx((1.01 ** 10 - 1) * 100, abs=0.01)
    assert r["6488"]["d5"] < 0 and r["2330"]["days"] == 19
    assert r["2330"]["d5_bench"] == pytest.approx((100 + 65 * 0.1) / (100 + 60 * 0.1) * 100 - 100, abs=0.01)
    st = rep["stats"][0]
    assert st["name"] == "老牛翻身" and st["n"] == 2 and st["d5"]["win"] == 50 and st["d5"]["beat"] == 50
    b = {x["name"]: x for x in rep["buckets"]}
    assert b["70 分以上"]["d5"]["win"] == 100 and b["未滿 50 分"]["d5"]["win"] == 0


def test_not_enough_days_yet(tmp_path):
    s = _store(tmp_path)
    w = W.Watch(s)
    w.add({"code": "2330", "added_date": DAYS[-2], "price": 100 * 1.01 ** 78, "entry": 70})
    r = w.report(s.bars())["rows"][0]
    assert r["days"] == 1 and "d3" not in r and r["ret"] == pytest.approx(1.0, abs=0.01)
    assert w.remove(r["id"]) and not w.remove(r["id"]) and w.report(s.bars())["rows"] == []


def test_watch_api(tmp_path):
    _store(tmp_path).close()
    c = TestClient(create_app(Settings(db_path=tmp_path / "w.db")))
    a = c.post("/api/watch", json={"code": "2330", "source": "老牛翻身"}).json()
    assert a["added_date"] == DAYS[-1] and a["price"] == pytest.approx(100 * 1.01 ** 79)
    assert 0 <= a["entry"] <= 100 and a["source"] == "老牛翻身"
    assert c.post("/api/watch", json={"code": "0000"}).status_code == 404
    rows = c.get("/api/watch").json()["rows"]
    assert [x["code"] for x in rows] == ["2330"] and rows[0]["days"] == 0
    assert c.delete(f"/api/watch/{a['id']}").json()["ok"]
    assert c.delete(f"/api/watch/{a['id']}").status_code == 404
