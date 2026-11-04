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
    assert a._buy(st, "d", {"code": "2330", "name": "x", "budget": 125_000, "reason": ""}, today, prev, {})[0] == "開盤漲停買不到"
    assert st["positions"] == {}
    today.loc["2330", "open"] = 101.0
    a._buy(st, "d", {"code": "2330", "name": "x", "budget": 125_000, "reason": ""}, today, prev, {})
    p = st["positions"]["2330"]
    assert p["shares"] == 1000 and st["cash"] == pytest.approx(A.START_CASH - 101_000 - 144)
    today.loc["2330", "open"] = 90.0
    assert a._sell(st, "d", {"code": "2330", "reason": ""}, today, prev, {})[0].startswith("開盤跌停賣不掉")
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


def test_fresh_start_from_latest_day(tmp_path):
    s = _db(tmp_path / "f.db", upto=75)
    a = A.Arena(s)
    a.reset(fresh=True)
    a.run()
    r = a.report()
    assert r["fresh"] and r["start"] == DAYS[74] and r["trading_days"] == 1
    assert r["nav"] == A.START_CASH and r["trades"] == 0 and r["live_since"] == DAYS[74]
    assert any(o["side"] == "buy" for o in r["orders"])   # 收盤後決定，隔天開盤買
    bars, inst, margin, _, _ = _market()
    s.upsert_bars([x for x in bars if x[1] == DAYS[75]])
    s.upsert_dataset("inst", [x for x in inst if x[1] == DAYS[75]])
    a.run()
    r = a.report()
    assert r["trades"] > 0 and r["trading_days"] == 2 and all(p["phase"] == "live" for p in r["curve"])


def test_journal_day_week_and_export(tmp_path):
    from radar import arena_journal as J
    s = _db(tmp_path / "j.db", upto=75)
    a = A.Arena(s)
    a.run()
    bars, inst, margin, _, _ = _market()
    s.upsert_bars([r for r in bars if r[1] in DAYS[75:]])
    s.upsert_dataset("inst", [r for r in inst if r[1] in DAYS[75:]])
    s.upsert_dataset("margin", [r for r in margin if r[1] in DAYS[75:]])
    a.run()
    j = J.Journal(s)
    rep = a.report()
    # 每天：持股還原要跟擂台的帳一致
    last = j.day()
    assert last["date"] == DAYS[-1] and last["phase"] == "實戰" and last["next"] is None
    assert {h["code"] for h in last["holdings"]} == {p["code"] for p in rep["positions"]}
    assert last["cash"] + sum(h["value"] for h in last["holdings"]) == pytest.approx(rep["nav"])
    assert [o["code"] for o in last["decisions"]] == [o["code"] for o in rep["orders"]]
    # 每筆委託都有紀錄：成交的筆數等於成交紀錄
    orders = pd.read_sql_query("SELECT * FROM arena_orders", s._conn)
    assert (orders["status"] == "成交").sum() == len(j.trades) and orders["decided"].notna().all()
    first, second = j.day(DAYS[60]), j.day(DAYS[61])
    assert first["prev"] is None and not first["buys"] and first["decisions"]   # 第一天收盤後才決定
    assert [b["code"] for b in second["buys"]] == [o["code"] for o in first["decisions"] if o["side"] == "buy"]
    # 每週：週報數字自洽
    w = j.week()
    assert w["ready"] and w["end"] == DAYS[-1] and w["start"] == J.monday(DAYS[-1])
    assert w["nav_end"] == pytest.approx(rep["nav"]) and len(w["daily"]) == w["days"]
    assert w["n_sell"] == w["wins"] + w["losses"]
    all_weeks = j.weeks()
    assert sum(j.week(x)["n_buy"] for x in all_weeks) == int((j.trades["side"] == "buy").sum())
    assert "百萬擂台週報" in J.week_md(w) and "收盤持股" in J.day_md(last)
    # 自動存檔
    root = J.export(s, tmp_path / "logs")
    assert root.name == f"開賽{DAYS[60]}"
    assert len(list((root / "每日").glob("*.md"))) == len(j.days)
    assert len(list((root / "每週").glob("*.md"))) == len(all_weeks)
    assert (root / "成交紀錄.csv").read_text(encoding="utf-8-sig").startswith("日期,代號,名稱,動作")


def test_journal_api(tmp_path):
    _db(tmp_path / "r.db").close()
    client = TestClient(create_app(Settings(db_path=tmp_path / "r.db")))
    r = client.get("/api/arena").json()
    for _ in range(100):
        if not r["running"] and r["ready"]:
            break
        time.sleep(0.1)
        r = client.get("/api/arena").json()
    r = client.get("/api/arena").json()
    assert r["log_dir"] and (tmp_path / "擂台紀錄").exists()
    d = client.get("/api/arena/journal").json()
    assert d["ready"] and d["days"][0] == d["date"] == DAYS[-1]
    d2 = client.get("/api/arena/journal", params={"date": DAYS[61]}).json()
    assert d2["date"] == DAYS[61] and d2["prev"] == DAYS[60]
    w = client.get("/api/arena/week").json()
    assert w["ready"] and w["weeks"][0] == w["start"]
    md = client.get("/api/arena/week.md", params={"start": w["weeks"][-1]})
    assert md.status_code == 200 and "百萬擂台週報" in md.text and "attachment" in md.headers["content-disposition"]
    assert "每日日誌" in client.get("/api/arena/day.md").text


def test_market_regime_modes():
    today = pd.DataFrame({"close": [97.0, 90.0, 101.0]}, index=["0050", "1101", "1102"])
    prev = pd.DataFrame({"close": [100.0, 100.0, 100.0]}, index=["0050", "1101", "1102"])
    df = pd.DataFrame({"price": [10, 10, 10, 10], "ma60": [9, 11, 11, 11]})
    r = A.market_regime(today, prev, df)
    assert r["mode"] == "pause" and "0050 -3.0%" in r["text"]
    calm = pd.DataFrame({"close": [100.5, 101.0, 99.0]}, index=["0050", "1101", "1102"])
    r2 = A.market_regime(calm, prev, df)
    assert r2["mode"] == "half" and r2["above_ma60"] == 25.0
    r3 = A.market_regime(calm, prev, pd.DataFrame({"price": [10, 10], "ma60": [9, 9]}))
    assert r3["mode"] == "normal"


def test_filter_pause_blocks_new_buys(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "FILTER_SINCE", DAYS[0])
    monkeypatch.setattr(A, "FILTER_BENCH_DROP", 100.0)   # 每天都觸發「暫停新進場」
    a = A.Arena(_db(tmp_path / "p.db"))
    st = a.run()
    r = a.report()
    assert not [t for t in r["recent_trades"] if t["side"] == "buy"] and not st["positions"]
    assert r["regime"]["mode"] == "pause"


def test_filter_rechecks_orders_decided_before_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "u.db"
    monkeypatch.setattr(A, "FILTER_SINCE", "9999-12-31")
    a = A.Arena(_db(path, upto=61))
    st = a.run()
    for o in st["orders"]:            # 舊版程式存下的委託沒有 regime 欄位
        o.pop("regime", None)
    a._put_state(st)
    assert any(o["side"] == "buy" for o in st["orders"])
    monkeypatch.setattr(A, "FILTER_SINCE", DAYS[0])
    monkeypatch.setattr(A, "FILTER_BENCH_DROP", 100.0)
    s = _db(path)
    A.Arena(s).run()
    rows = s._conn.execute("SELECT status FROM arena_orders WHERE exec_date=? AND side='buy'", (DAYS[61],)).fetchall()
    assert rows and all(r[0].startswith("大盤濾網") for r in rows)
