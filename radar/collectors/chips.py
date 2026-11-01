"""法人籌碼與基本面：證交所、櫃買中心官方公開資料。

- 三大法人買賣超（每日）：TWSE T86、TPEx 三大法人買賣明細
- 融資融券餘額（每日）：TWSE MI_MARGN、TPEx 融資融券餘額
- 本益比、殖利率、股價淨值比（每日）：TWSE BWIBBU_d、TPEx 本益比查詢
- 月營收（每月）：TWSE / TPEx OpenAPI 上市、上櫃公司每月營業收入

欄位一律用「名稱」比對，不依賴固定順序；交易所改版時會丟出看得懂的錯誤。
"""
from __future__ import annotations

from datetime import date
from typing import Callable

from .exchanges import NoData
from .http import Http
from .parsing import is_common_code, to_float, to_int

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_PE = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
TWSE_REVENUE = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
TPEX_MARGIN = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
TPEX_PE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
TPEX_INST_LEGACY = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
# 上櫃月營收：有些網路解析不到 openapi.tpex.org.tw，所以依序嘗試三個官方位址
TPEX_REVENUE_URLS = [
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    "https://openapi.tpex.org.tw/v1/mopsfin_t187ap05_O",
]
TPEX_REVENUE_CSV = "https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv"


def _norm(f) -> str:
    return str(f).replace(" ", "").replace("　", "").replace("<br>", "").replace("\n", "")


def iter_tables(payload) -> list[tuple[list[str], list[list]]]:
    """把各種回應格式（fields/data、tables[]、fieldsN/dataN）整理成 (欄位, 資料) 清單。"""
    out = []
    if not isinstance(payload, dict):
        return out
    if isinstance(payload.get("fields"), list):
        out.append((payload["fields"], payload.get("data") or []))
    for t in payload.get("tables") or []:
        if isinstance(t, dict) and t.get("fields"):
            out.append((t["fields"], t.get("data") or []))
    for key, fields in payload.items():
        if key.startswith("fields") and key != "fields" and isinstance(fields, list):
            out.append((fields, payload.get("data" + key[len("fields"):]) or []))
    return [(list(map(_norm, f)), d) for f, d in out]


def _find(fields: list[str], pred: Callable[[str], bool], nth: int = 0) -> int | None:
    hits = [i for i, f in enumerate(fields) if pred(f)]
    return hits[nth] if len(hits) > nth else None


def _code_col(fields):
    return _find(fields, lambda f: f in ("證券代號", "代號", "股票代號"))


def _check_stat(payload) -> None:
    stat = str(payload.get("stat", "OK")) if isinstance(payload, dict) else "OK"
    if stat.upper() not in ("OK", "") and "ok" not in stat.lower():
        raise NoData(stat)


# ---------- 三大法人 ----------
# 櫃買中心三大法人表的固定欄位順序（第幾個「買賣超」欄）：
# 0 外資及陸資(不含外資自營商)、1 外資自營商、2 外資合計、3 投信、4 自營商(自行)、5 自營商(避險)、6 自營商合計、7 三大法人合計
TPEX_NET_ORDER = {"foreign": 0, "trust": 3, "dealer": 6}


def _inst_rows(data, day, ic, i_f, i_t, i_d) -> list[tuple]:
    rows = []
    for r in data:
        code = str(r[ic]).strip()
        if is_common_code(code):
            rows.append((code, day.isoformat(), to_int(r[i_f]), to_int(r[i_t]),
                         to_int(r[i_d]) if i_d is not None else None))
    return rows


def parse_inst(payload, day: date) -> list[tuple]:
    _check_stat(payload)
    seen = []
    for fields, data in iter_tables(payload):
        seen.append(fields)
        ic = _code_col(fields)
        i_f = _find(fields, lambda f: ("外資" in f or "外陸資" in f) and "買賣超" in f)
        i_t = _find(fields, lambda f: "投信" in f and "買賣超" in f)
        i_d = (_find(fields, lambda f: f in ("自營商買賣超股數", "自營商-買賣超股數", "自營商(合計)-買賣超股數",
                                            "自營商合計-買賣超股數"))
               or _find(fields, lambda f: f.startswith("自營商") and "買賣超" in f
                        and "自行" not in f and "避險" not in f))
        if ic is not None and (i_f is None or i_t is None):
            # 欄位名稱只寫「買賣超股數」、分組標題在別處：依出現順序對應
            nets = [i for i, f in enumerate(fields) if "買賣超" in f]
            if len(nets) >= 7:
                i_f, i_t, i_d = (nets[TPEX_NET_ORDER[k]] for k in ("foreign", "trust", "dealer"))
        if ic is None or i_f is None or i_t is None:
            continue
        if not data:
            raise NoData("empty")
        return _inst_rows(data, day, ic, i_f, i_t, i_d)
    aa = payload.get("aaData") if isinstance(payload, dict) else None
    if aa:  # 櫃買中心舊版介面：固定 24 欄
        if len(aa[0]) < 23:
            raise ValueError(f"三大法人舊版格式欄數不符：{len(aa[0])}")
        return _inst_rows(aa, day, 0, 4, 13, 22)
    if isinstance(payload, dict) and ("aaData" in payload or payload.get("iTotalRecords") == 0):
        raise NoData("empty")
    hint = "；".join("、".join(f[:8]) for f in seen[:2]) or f"回應欄位 {list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__}"
    raise ValueError(f"三大法人欄位格式改變，無法辨識（{hint}）")


# ---------- 融資融券 ----------
def parse_margin(payload, day: date) -> list[tuple]:
    _check_stat(payload)
    for fields, data in iter_tables(payload):
        ic = _code_col(fields)
        if ic is None:
            continue
        # TWSE：兩個「今日餘額」，前者融資、後者融券
        i_m = _find(fields, lambda f: f == "今日餘額")
        i_s = _find(fields, lambda f: f == "今日餘額", 1)
        if i_m is None:  # TPEx：資餘額 / 券餘額
            i_m = _find(fields, lambda f: f.startswith("資餘額") or f.startswith("融資餘額"))
            i_s = _find(fields, lambda f: f.startswith("券餘額") or f.startswith("融券餘額"))
        if i_m is None:
            continue
        if not data:
            raise NoData("empty")
        rows = []
        for r in data:
            code = str(r[ic]).strip()
            if is_common_code(code):
                rows.append((code, day.isoformat(), to_int(r[i_m]),
                             to_int(r[i_s]) if i_s is not None else None))
        return rows
    raise NoData("找不到融資融券彙總表")


# ---------- 本益比 / 殖利率 / 淨值比 ----------
def parse_valuation(payload, day: date) -> list[tuple]:
    _check_stat(payload)
    for fields, data in iter_tables(payload):
        ic = _code_col(fields)
        i_pe = _find(fields, lambda f: f.startswith("本益比"))
        i_pb = _find(fields, lambda f: f.startswith("股價淨值比"))
        i_dy = _find(fields, lambda f: f.startswith("殖利率"))
        if ic is None or i_pe is None:
            continue
        if not data:
            raise NoData("empty")
        rows = []
        for r in data:
            code = str(r[ic]).strip()
            if is_common_code(code):
                rows.append((code, day.isoformat(), to_float(r[i_pe]),
                             to_float(r[i_pb]) if i_pb is not None else None,
                             to_float(r[i_dy]) if i_dy is not None else None))
        return rows
    raise ValueError("本益比欄位格式改變，無法辨識")


# ---------- 月營收（OpenAPI，JSON 陣列） ----------
def _roc_ym(s: str) -> str | None:
    s = str(s).strip()
    if len(s) < 4 or not s.isdigit():
        return None
    year, month = int(s[:-2]) + 1911, int(s[-2:])
    return f"{year:04d}-{month:02d}" if 1 <= month <= 12 else None


def _get(obj: dict, *keys):
    norm = {_norm(k): v for k, v in obj.items()}
    for k in keys:
        if k in norm:
            return norm[k]
    return None


def parse_revenue(items) -> tuple[list[tuple], list[tuple]]:
    """回傳 (營收列, 產業別列)。"""
    if not isinstance(items, list) or not items:
        raise NoData("沒有月營收資料")
    rev, ind = [], []
    for o in items:
        code = str(_get(o, "公司代號") or "").strip()
        if not is_common_code(code):
            continue
        ym = _roc_ym(_get(o, "資料年月") or "")
        if not ym:
            continue
        rev.append((code, ym, to_float(_get(o, "營業收入-當月營收")),
                    to_float(_get(o, "營業收入-上月比較增減(%)")),
                    to_float(_get(o, "營業收入-去年同月增減(%)")),
                    to_float(_get(o, "累計營業收入-前期比較增減(%)"))))
        industry = str(_get(o, "產業別") or "").strip()
        if industry and not industry.isdigit():
            ind.append((code, industry))
    if not rev:
        raise ValueError("月營收欄位格式改變，無法辨識")
    return rev, ind


# ---------- 下載 ----------
def _tw(day: date) -> str:
    return day.strftime("%Y%m%d")


def _tp(day: date) -> str:
    return day.strftime("%Y/%m/%d")


def fetch_inst(http: Http, market: str, day: date):
    if market == "TWSE":
        p = http.get_json(TWSE_T86, {"date": _tw(day), "selectType": "ALLBUT0999", "response": "json"})
        return parse_inst(p, day)
    try:
        p = http.get_json(TPEX_INST, {"type": "Daily", "sect": "EW", "date": _tp(day), "response": "json"},
                          allow_insecure_fallback=True)
        return parse_inst(p, day)
    except NoData:
        raise
    except Exception as first:  # noqa: BLE001  新版介面不行就改用舊版
        roc = f"{day.year - 1911}/{day:%m/%d}"
        try:
            p = http.get_json(TPEX_INST_LEGACY, {"l": "zh-tw", "se": "EW", "t": "D", "d": roc},
                              allow_insecure_fallback=True)
            return parse_inst(p, day)
        except NoData:
            raise
        except Exception as second:  # noqa: BLE001
            raise ValueError(f"新版：{first}／舊版：{second}") from second


def fetch_margin(http: Http, market: str, day: date):
    if market == "TWSE":
        p = http.get_json(TWSE_MARGIN, {"date": _tw(day), "selectType": "STOCK", "response": "json"})
    else:
        p = http.get_json(TPEX_MARGIN, {"date": _tp(day), "response": "json"}, allow_insecure_fallback=True)
    return parse_margin(p, day)


def fetch_valuation(http: Http, market: str, day: date):
    if market == "TWSE":
        p = http.get_json(TWSE_PE, {"date": _tw(day), "selectType": "ALL", "response": "json"})
    else:
        p = http.get_json(TPEX_PE, {"date": _tp(day), "response": "json"}, allow_insecure_fallback=True)
    return parse_valuation(p, day)


def parse_revenue_csv(text: str):
    import csv
    import io
    return parse_revenue(list(csv.DictReader(io.StringIO(text.lstrip("\ufeff")))))


def fetch_revenue(http: Http, market: str):
    if market == "TWSE":
        return parse_revenue(http.get_json(TWSE_REVENUE, allow_insecure_fallback=True))
    errors = []
    for url in TPEX_REVENUE_URLS:
        try:
            return parse_revenue(http.get_json(url, allow_insecure_fallback=True))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url.split('/')[2]}：{str(e)[:80]}")
    try:
        return parse_revenue_csv(http.get_text(TPEX_REVENUE_CSV, allow_insecure_fallback=True))
    except Exception as e:  # noqa: BLE001
        errors.append(f"mopsfin：{str(e)[:80]}")
    raise ValueError("上櫃月營收三個來源都失敗：" + "；".join(errors))


DAILY_FETCHERS = {"inst": fetch_inst, "margin": fetch_margin}
