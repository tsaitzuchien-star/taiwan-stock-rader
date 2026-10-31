"""證交所 (TWSE) 與櫃買中心 (TPEx) 官方每日收盤行情。

一個請求就拿到某一天「全市場」所有股票的開高低收量，
取代原本逐檔向 Yahoo / 鉅亨網下載的做法。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .http import Http
from .parsing import find_col, is_common_code, to_float, to_int

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TPEX_LEGACY_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"


@dataclass
class Quote:
    code: str
    name: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None  # 股

    def bar_row(self, day: date) -> tuple:
        return (self.code, day.isoformat(), self.open, self.high, self.low, self.close, self.volume)


class NoData(Exception):
    """該日沒有資料（休市日，或當日資料尚未公布）。"""


def _rows_from_table(fields: list[str], data: list[list]) -> list[Quote]:
    i_code = find_col(fields, "證券代號", "代號")
    i_name = find_col(fields, "證券名稱", "名稱")
    i_open = find_col(fields, "開盤價", "開盤")
    i_high = find_col(fields, "最高價", "最高")
    i_low = find_col(fields, "最低價", "最低")
    i_close = find_col(fields, "收盤價", "收盤")
    i_vol = find_col(fields, "成交股數")
    idx = [i_code, i_name, i_open, i_high, i_low, i_close, i_vol]
    if any(i is None for i in idx):
        raise ValueError(f"欄位格式改變，無法辨識：{fields}")
    out = []
    for row in data:
        code = str(row[i_code]).strip()
        if not is_common_code(code):
            continue
        out.append(Quote(
            code=code,
            name=str(row[i_name]).strip(),
            open=to_float(row[i_open]),
            high=to_float(row[i_high]),
            low=to_float(row[i_low]),
            close=to_float(row[i_close]),
            volume=to_int(row[i_vol]),
        ))
    return out


def parse_twse(payload: dict) -> list[Quote]:
    if str(payload.get("stat", "")).upper() != "OK":
        raise NoData(payload.get("stat", "no stat"))
    tables: list[tuple[list, list]] = []
    for t in payload.get("tables", []) or []:
        tables.append((t.get("fields") or [], t.get("data") or []))
    # 舊版格式：fields9 / data9 這類成對欄位
    for key, fields in payload.items():
        if key.startswith("fields") and isinstance(fields, list):
            tables.append((fields, payload.get("data" + key[len("fields"):], [])))
    for fields, data in tables:
        norm = [str(f).replace(" ", "") for f in fields]
        if "證券代號" in norm and "收盤價" in norm:
            quotes = _rows_from_table(fields, data)
            if not quotes:
                raise NoData("empty table")
            return quotes
    raise NoData("找不到每日收盤行情表")


def parse_tpex(payload: dict) -> list[Quote]:
    tables = payload.get("tables") or []
    for t in tables:
        fields, data = t.get("fields") or [], t.get("data") or []
        if fields and data and find_col(fields, "代號") is not None and find_col(fields, "收盤") is not None:
            return _rows_from_table(fields, data)
    # 舊版介面：aaData，依固定欄位順序
    aa = payload.get("aaData")
    if aa:
        fields = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數"]
        return _rows_from_table(fields, [r[:9] for r in aa])
    raise NoData(payload.get("stat") or "無資料")


def fetch_twse(http: Http, day: date) -> list[Quote]:
    payload = http.get_json(TWSE_URL, {"date": day.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"})
    return parse_twse(payload)


def fetch_tpex(http: Http, day: date) -> list[Quote]:
    try:
        payload = http.get_json(
            TPEX_URL, {"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
            allow_insecure_fallback=True)
        return parse_tpex(payload)
    except NoData:
        raise
    except Exception:
        roc = f"{day.year - 1911}/{day:%m/%d}"
        payload = http.get_json(TPEX_LEGACY_URL, {"l": "zh-tw", "d": roc, "se": "EW"},
                                allow_insecure_fallback=True)
        return parse_tpex(payload)


FETCHERS = {"TWSE": fetch_twse, "TPEX": fetch_tpex}
