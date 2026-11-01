"""法人籌碼、基本面解析與四因子評分。"""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from radar import alpha, collect
from radar.api.server import create_app
from radar.collectors import chips
from radar.collectors.exchanges import NoData
from radar.config import Settings
from radar.storage import Store

DAY = date(2026, 10, 30)

T86_FIELDS = ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
              "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
              "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
              "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
              "自營商買賣超股數(避險)", "三大法人買賣超股數"]


def test_parse_twse_t86():
    row = ["2330", "台積電", "10,000", "4,000", "6,000", "0", "0", "0", "3,000", "1,000", "2,000", "-500",
           "0", "0", "0", "0", "0", "0", "7,500"]
    rows = chips.parse_inst({"stat": "OK", "fields": T86_FIELDS, "data": [row, ["00878"] + row[1:]]}, DAY)
    assert rows == [("2330", "2026-10-30", 6000, 2000, -500)]


def test_parse_tpex_inst_tables_format():
    fields = ["代號", "名稱", "外資及陸資(不含外資自營商)-買進股數", "外資及陸資(不含外資自營商)-賣出股數",
              "外資及陸資(不含外資自營商)-買賣超股數", "外資自營商-買賣超股數", "外資及陸資-買賣超股數",
              "投信-買進股數", "投信-賣出股數", "投信-買賣超股數", "自營商(自行買賣)-買賣超股數",
              "自營商(避險)-買賣超股數", "自營商-買賣超股數", "三大法人買賣超股數合計"]
    row = ["6488", "環球晶", "1", "1", "-1,000", "0", "-1,000", "0", "0", "3,000", "1", "1", "200", "2,200"]
    rows = chips.parse_inst({"tables": [{"fields": fields, "data": [row]}]}, DAY)
    assert rows == [("6488", "2026-10-30", -1000, 3000, 200)]


def test_parse_margin_twse_and_tpex():
    tw_fields = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                 "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]
    payload = {"stat": "OK", "tables": [
        {"title": "信用交易統計", "fields": ["項目", "買進"], "data": [["融資(交易單位)", "1"]]},
        {"title": "融資融券彙總", "fields": tw_fields,
         "data": [["2330", "台積電", "1", "1", "0", "9,000", "9,100", "0", "1", "1", "0", "300", "310", "0", "0", ""]]}]}
    assert chips.parse_margin(payload, DAY) == [("2330", "2026-10-30", 9100, 310)]
    tp_fields = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)",
                 "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額"]
    tp = {"tables": [{"fields": tp_fields, "data": [["6488", "環球晶", "1", "1", "1", "1", "2,345", "0", "1", "1",
                                                     "1", "1", "1", "1", "67"]]}]}
    assert chips.parse_margin(tp, DAY) == [("6488", "2026-10-30", 2345, 67)]


def test_parse_valuation_and_holiday():
    fields = ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]
    rows = chips.parse_valuation({"stat": "OK", "fields": fields,
                                  "data": [["2330", "台積電", "1005", "1.59", "114", "22.10", "7.01", "115/2"],
                                           ["1101", "台泥", "30", "4.00", "114", "-", "0.9", "115/2"]]}, DAY)
    assert rows[0] == ("2330", "2026-10-30", 22.1, 7.01, 1.59) and rows[1][2] is None
    with pytest.raises(NoData):
        chips.parse_valuation({"stat": "很抱歉，沒有符合條件的資料!"}, DAY)


def test_parse_revenue_real_sample():
    items = [{"出表日期": "1151017", "資料年月": "11509", "公司代號": "1101", "公司名稱": "台泥", "產業別": "水泥工業",
              "營業收入-當月營收": "14321556", "營業收入-上月營收": "13515534", "營業收入-去年當月營收": "13306676",
              "營業收入-上月比較增減(%)": "5.963671135746468", "營業收入-去年同月增減(%)": "7.626848357921993",
              "累計營業收入-當月累計營收": "113048525", "累計營業收入-去年累計營收": "109438297",
              "累計營業收入-前期比較增減(%)": "3.298870778298021", "備註": "-"}]
    rev, ind = chips.parse_revenue(items)
    assert rev[0][:3] == ("1101", "2026-09", 14321556.0) and rev[0][4] == pytest.approx(7.6268, 1e-3)
    assert ind == [("1101", "水泥工業")]


def test_streak():
    assert alpha._streak([1, -2, 3, 4, 5]) == 3
    assert alpha._streak([5, -1, -1]) == -2
    assert alpha._streak([1, 0]) == 0


@pytest.fixture()
def db(tmp_path):
    store = Store(tmp_path / "radar.db")
    days = pd.bdate_range("2026-07-01", periods=70)
    store.upsert_stocks([("1101", "台泥", "TWSE"), ("6488", "環球晶", "TPEX"), ("2330", "台積電", "TWSE")])
    rows = []
    for i, d in enumerate(days):
        ds = d.date().isoformat()
        rows += [("1101", ds, 30, 31, 29, 30 + i * 0.1, 2_000_000), ("6488", ds, 400, 404, 396, 400 - i, 1_000_000),
                 ("2330", ds, 1000, 1010, 990, 1000 + i, 30_000_000)]
    store.upsert_bars(rows)
    inst, margin = [], []
    for i, d in enumerate(days[-10:]):
        ds = d.date().isoformat()
        inst += [("1101", ds, 300_000, 200_000, 0), ("6488", ds, -100_000, -50_000, 0), ("2330", ds, 1_000_000, 0, 0)]
        margin += [("1101", ds, 5000 - i * 100, 50), ("6488", ds, 3000 + i * 100, 10), ("2330", ds, 20000, 100)]
    store.upsert_dataset("inst", inst)
    store.upsert_dataset("margin", margin)
    last = days[-1].date().isoformat()
    store.upsert_dataset("valuation", [("1101", last, 10.0, 0.9, 6.0), ("6488", last, 40.0, 3.0, 1.0),
                                       ("2330", last, 22.0, 7.0, 1.5)])
    store.upsert_dataset("revenue", [("1101", "2026-09", 1e7, 5.0, 45.0, 20.0), ("6488", "2026-09", 5e6, -3.0, -10.0, -5.0)])
    store.upsert_dataset("company", [("1101", "水泥工業"), ("6488", "半導體業"), ("2330", "半導體業")])
    store.close()
    return tmp_path / "radar.db"


def test_alpha_api(db):
    c = TestClient(create_app(Settings(db_path=db)))
    r = c.get("/api/alpha").json()
    assert r["coverage"]["with_chips"] == 3 and r["coverage"]["revenue_ym"] == "2026-09"
    codes = [x["code"] for x in r["rows"]]
    assert codes[0] == "1101" and codes[-1] == "6488"
    top = r["rows"][0]
    assert top["t_streak"] == 10 and top["f5"] == 1500 and "土洋同買" in top["tags"] and "營收爆發" in top["tags"]
    assert "散戶退場" in top["tags"] and "高殖利率" in top["tags"]
    assert [x["code"] for x in c.get("/api/alpha", params={"preset": "trust"}).json()["rows"]] == ["1101"]
    assert c.get("/api/alpha", params={"industry": "半導體業"}).json()["total"] == 2
    sec = {s["industry"]: s for s in c.get("/api/alpha/sectors").json()}
    assert sec["半導體業"]["n"] == 2
    flows = c.get("/api/market/flows").json()
    assert len(flows) == 10 and flows[-1]["foreign"] > 0
    ch = c.get("/api/stock/6488/chips").json()
    assert len(ch["inst"]) == 10 and ch["inst"][-1]["foreign"] == -100 and ch["score"]["f_streak"] == -10
    assert ch["margin"][-1]["margin_bal"] == 3900 and ch["revenue"][0]["yoy"] == -10.0


def test_alpha_api_without_chip_data(tmp_path):
    store = Store(tmp_path / "r.db")
    store.close()
    c = TestClient(create_app(Settings(db_path=tmp_path / "r.db")))
    assert c.get("/api/alpha").json()["rows"] == []
    assert c.get("/api/market/flows").json() == []


def test_update_chips_logs_and_isolates_errors(db):
    store = Store(db)
    calls = []

    def inst(http, market, day):
        calls.append((market, day))
        if market == "TPEX":
            raise ValueError("格式改變")
        return [("1101", day.isoformat(), 1000, 1000, 0)]

    fetchers = {"inst": inst, "margin": lambda h, m, d: [], "valuation": lambda h, m, d: [],
                "revenue": lambda h, m: ([("1101", "2026-10", 1.0, 1.0, 1.0, 1.0)], [("1101", "水泥")])}
    report = collect.CollectReport()
    s = Settings(db_path=db, chip_trading_days=5)
    collect.update_chips(store, s, http=None, today=date(2026, 10, 31), progress=lambda *a: None,
                         report=report, fetchers=fetchers)
    assert sum(1 for m, _ in calls if m == "TWSE") == 5
    assert sum(1 for m, _ in calls if m == "TPEX") == 3        # 連錯 3 次就停
    assert any("格式改變" in e for e in report.errors)
    n = len(calls)
    collect.update_chips(store, s, http=None, today=date(2026, 10, 31), progress=lambda *a: None,
                         report=collect.CollectReport(), fetchers=fetchers)
    assert sum(1 for m, _ in calls[n:] if m == "TWSE") == 0     # 已抓過的不重抓
    assert store.dataset("revenue")["ym"].max() == "2026-10"
