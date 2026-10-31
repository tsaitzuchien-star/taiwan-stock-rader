from datetime import date

import pytest

from radar.collectors.exchanges import NoData, parse_tpex, parse_twse

TWSE_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價",
               "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價",
               "最後揭示賣量", "本益比"]


def twse_payload(rows):
    return {"stat": "OK", "date": "20261030", "tables": [
        {"title": "價格指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "23,000.00"]]},
        {"title": "每日收盤行情(全部(不含權證、牛熊證))", "fields": TWSE_FIELDS, "data": rows},
    ]}


def test_parse_twse_reads_quotes_and_skips_non_common_codes():
    rows = [
        ["2330", "台積電", "25,123,456", "30,000", "25,000,000,000", "1,000.00", "1,010.00", "995.00",
         "1,005.00", "<p>+</p>", "5.00", "1,005.00", "100", "1,010.00", "50", "25.00"],
        ["1234", "停牌股", "0", "0", "0", "--", "--", "--", "--", "", "0.00", "", "", "", "", ""],
        ["00878", "國泰永續高股息", "1,000", "1", "20,000", "20.0", "20.0", "20.0", "20.0", "", "", "", "", "", "", ""],
    ]
    quotes = parse_twse(twse_payload(rows))
    assert [q.code for q in quotes] == ["2330", "1234"]
    q = quotes[0]
    assert (q.open, q.high, q.low, q.close, q.volume) == (1000.0, 1010.0, 995.0, 1005.0, 25123456)
    assert quotes[1].close is None
    assert q.bar_row(date(2026, 10, 30))[1] == "2026-10-30"


def test_parse_twse_legacy_fields9_format():
    payload = {"stat": "OK", "fields9": TWSE_FIELDS, "data9": [
        ["2317", "鴻海", "1,000", "1", "1", "200", "201", "199", "200.5", "", "", "", "", "", "", ""]]}
    assert parse_twse(payload)[0].close == 200.5


def test_parse_twse_holiday():
    with pytest.raises(NoData):
        parse_twse({"stat": "很抱歉，沒有符合條件的資料!"})


def test_parse_tpex_new_and_legacy_formats():
    fields = ["代號", "名稱", "收盤 ", "漲跌", "開盤 ", "最高 ", "最低", "均價 ", "成交股數  ", "成交金額(元)", "成交筆數 "]
    new = {"stat": "ok", "tables": [{"fields": fields, "data": [
        ["6488", "環球晶", "500.00", "+5.00", "495.00", "505.00", "490.00", "498.00", "1,234,000", "1", "1"]]}]}
    q = parse_tpex(new)[0]
    assert (q.code, q.open, q.close, q.volume) == ("6488", 495.0, 500.0, 1234000)

    legacy = {"iTotalRecords": 1, "aaData": [
        ["3105", "穩懋", "150.00", "-1.00", "151.00", "152.00", "149.00", "150.10", "2,000,000", "x", "x"]]}
    assert parse_tpex(legacy)[0].volume == 2000000


def test_parse_tpex_empty_is_nodata():
    with pytest.raises(NoData):
        parse_tpex({"stat": "ok", "tables": [{"fields": ["代號"], "data": []}]})
