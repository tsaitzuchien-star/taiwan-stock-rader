"""把元大 Spark API 回傳的 .NET 物件轉成 Python 資料。

這裡只用「讀屬性」的方式取值，不直接依賴 .NET，所以可以在沒有 DLL 的環境做單元測試。
欄位名稱依元大 IO_Spec 文件（GetStoreSummary、GetWatchListAll、SubscribeWatchlistAll 等）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

# 元大市場代碼 <-> 本專案資料庫的市場代號
MARKET_FROM_YUANTA = {"TWSE": "TWSE", "1": "TWSE", "TWOTC": "TPEX", "2": "TPEX"}
TRADE_KIND = {0: "現股", 3: "融資", 4: "融券", 6: "借券"}

# 市價買 / 市價賣在報價欄位中的特殊值
_MARKET_ORDER_PRICES = {999999999.0, -999999999.0}

HOLDING_COLUMNS = ["股票代號", "股票名稱", "市場", "交易種類", "股數", "可交易股數",
                   "成交均價", "持有成本", "市價", "股票市值", "未實現損益", "報酬率(%)"]
QUOTE_COLUMNS = ["股票代號", "股票名稱", "市場", "成交價", "漲跌(%)", "開盤", "最高", "最低",
                 "昨收", "總量(張)", "買價", "賣價", "漲停", "跌停", "時間"]


def field(obj: Any, name: str, default: Any = None) -> Any:
    """讀 .NET 物件欄位。一般屬性直接讀；元大有些欄位（例如 Cost）要透過反射才讀得到。"""
    try:
        return getattr(obj, name)
    except AttributeError:
        pass
    try:  # pythonnet 物件：用反射讀非公開欄位
        from System.Reflection import BindingFlags  # type: ignore
        flags = (BindingFlags.Instance | BindingFlags.Public
                 | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
        f = obj.GetType().GetField(name, flags)
        if f is not None:
            return f.GetValue(obj)
    except Exception:
        pass
    return default


def num(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x in _MARKET_ORDER_PRICES:
        return default
    return x


def market(v: Any) -> str:
    s = str(v).strip()
    if s.isdigit():
        return MARKET_FROM_YUANTA.get(s, s)
    return MARKET_FROM_YUANTA.get(s.split(".")[-1], s)


def items(lst: Any) -> list:
    """.NET List<T> 或 Python list 都轉成 Python list。"""
    if lst is None:
        return []
    return list(lst)


def yuanta_time(t: Any) -> str:
    """TYuantaTime(bytHour, bytMin, bytSec, ushtMSec) -> 'HH:MM:SS'。"""
    if t is None:
        return ""
    h, m, s = (field(t, k, 0) for k in ("bytHour", "bytMin", "bytSec"))
    try:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    except (TypeError, ValueError):
        return str(t)


def to_date(v: Any) -> date | None:
    """.NET DateTime、Python datetime 或 'yyyy/MM/dd' 字串 -> date。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    y, mo, d = field(v, "Year"), field(v, "Month"), field(v, "Day")
    if y is not None and mo is not None and d is not None:
        return date(int(y), int(mo), int(d))
    s = str(v).strip().split(" ")[0].replace("/", "-")
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ---------------- 登入 ----------------
def parse_login(result: Any) -> dict:
    status = field(result, "LoginStatus")
    code = str(field(status, "MsgCode", "") or "")
    msg = str(field(status, "MsgContent", "") or "")
    accounts = [str(field(x, "Account", "")) for x in items(field(result, "LoginList"))]
    ok = code in ("0001", "00001") or (bool(accounts) and not code)
    return {"ok": ok, "code": code, "message": msg, "accounts": accounts}


# ---------------- 帳務 ----------------
def parse_store_summary(result: Any) -> pd.DataFrame:
    rows = []
    for x in items(field(result, "StkStoreList")):
        qty = num(field(x, "StockQty"), 0) or 0
        if qty == 0:
            continue
        cost = num(field(x, "Cost"), 0) or 0
        pl = num(field(x, "ReturnAmt"), 0) or 0
        kind = field(x, "TradeKind", 0)
        try:
            kind = TRADE_KIND.get(int(kind), str(kind))
        except (TypeError, ValueError):
            kind = str(kind)
        rows.append({
            "股票代號": str(field(x, "StkCode", "")).strip(),
            "股票名稱": str(field(x, "StkName", "")).strip(),
            "市場": market(field(x, "MarketNo", "")),
            "交易種類": kind,
            "股數": int(qty),
            "可交易股數": int(num(field(x, "TradingQty"), 0) or 0),
            "成交均價": num(field(x, "Price")),
            "持有成本": cost,
            "市價": num(field(x, "MarketPrice")),
            "股票市值": num(field(x, "MarketAmt"), 0),
            "未實現損益": pl,
            "報酬率(%)": round(pl / cost * 100, 2) if cost else None,
        })
    return pd.DataFrame(rows, columns=HOLDING_COLUMNS)


def parse_unrealized_detail(result: Any) -> pd.DataFrame:
    rows = []
    for x in items(field(result, "UnGainLossDetailList")):
        rows.append({
            "股票代號": str(field(x, "StkCode", "")).strip(),
            "成交日": str(field(x, "TradeDate", "")),
            "交易種類": TRADE_KIND.get(_int(field(x, "TradeKind")), ""),
            "股數": _int(field(x, "StockQty")),
            "成交價": num(field(x, "Price")),
            "持有成本": num(field(x, "Cost")),
            "股票市值": num(field(x, "MarketAmt")),
            "未實現損益": num(field(x, "ReturnAmt")),
        })
    return pd.DataFrame(rows)


def parse_realized(result: Any) -> pd.DataFrame:
    rows = []
    for x in items(field(result, "RealizedGainLossList")):
        rows.append({
            "成交日": str(field(x, "TradeDate", "")),
            "股票代號": str(field(x, "StkCode", "")).strip(),
            "市場": market(field(x, "MarketNo", "")),
            "交易種類": str(field(x, "TradeKind", "")),
            "成交價": num(field(x, "Price")),
            "股數": _int(field(x, "Qty")),
            "已實現損益": num(field(x, "ProfitLoss")),
            "手續費": num(field(x, "Charge")),
            "交易稅": num(field(x, "Tax")),
            "成本": num(field(x, "Cost")),
        })
    return pd.DataFrame(rows)


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------- 行情 ----------------
def parse_watchlist_snapshot(result: Any) -> dict[str, dict]:
    """GetWatchListAll（報價表查詢）-> {股票代號: 報價}。總量單位：張。"""
    out = {}
    for x in items(field(result, "QueryWatchList")):
        code = str(field(x, "StkCode", "")).strip()
        if not code:
            continue
        out[code] = {
            "code": code,
            "name": str(field(x, "StkName", "") or "").strip(),
            "market": market(field(x, "MarketNo", "")),
            "price": num(field(x, "DealPrice")),
            "open": num(field(x, "OpenPrice")),
            "high": num(field(x, "HighPrice")),
            "low": num(field(x, "LowPrice")),
            "prev_close": num(field(x, "YstPrice")),
            "ref_price": num(field(x, "OpenRefPrice")),
            "limit_up": num(field(x, "UpStopPrice")),
            "limit_down": num(field(x, "DownStopPrice")),
            "bid": num(field(x, "BuyPrice")),
            "ask": num(field(x, "SellPrice")),
            "total_vol": num(field(x, "TotalVol"), 0),
            "time": _time_text(field(x, "Time")),
        }
    return out


def _time_text(v: Any) -> str:
    if v is None:
        return ""
    if field(v, "bytHour") is not None:
        return yuanta_time(v)
    h, m, s = field(v, "Hour"), field(v, "Minute"), field(v, "Second")
    if h is not None:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"
    return str(v)


# enumQuoteIndexType 的單一欄位（SubscribeWatchlistAll 推播用）
_INDEX_FIELDS = {0: "open", 1: "high", 2: "low", 3: "bid", 5: "ask", 7: "price",
                 10: "total_vol", 201: "prev_close", 202: "limit_up", 203: "limit_down",
                 254: "ref_price"}


def _index_no(flag: Any) -> int | None:
    try:
        return int(flag)
    except (TypeError, ValueError):
        s = str(flag)
        digits = "".join(ch for ch in s if ch.isdigit())
        return int(digits) if digits else None


def apply_watchlist_push(quotes: dict[str, dict], result: Any) -> str | None:
    """把 SubscribeWatchlistAll 的一筆推播更新進 quotes，回傳股票代號。"""
    code = str(field(result, "StkCode", "") or "").strip()
    if not code:
        return None
    q = quotes.setdefault(code, {"code": code, "market": market(field(result, "MarketType", ""))})
    flag = _index_no(field(result, "IndexFlag"))
    if flag == 28:
        f28 = field(result, "IndexFlag_28")
        q["bid"] = num(field(f28, "BuyPrice"), q.get("bid"))
        q["ask"] = num(field(f28, "SellPrice"), q.get("ask"))
    elif flag == 29:
        f29 = field(result, "IndexFlag_29")
        price = num(field(f29, "Deal"))
        if price:
            q["price"] = price
            q["high"] = max(q.get("high") or price, price)
            q["low"] = min(q.get("low") or price, price)
            if not q.get("open"):
                q["open"] = price
        q["total_vol"] = num(field(f29, "TotalVol"), q.get("total_vol"))
        q["time"] = yuanta_time(field(f29, "Time"))
    elif flag in _INDEX_FIELDS:
        v = num(field(result, "Value"))
        if v is not None:
            q[_INDEX_FIELDS[flag]] = v
    return code


def quotes_frame(quotes: dict[str, dict], names: dict[str, str] | None = None) -> pd.DataFrame:
    names = names or {}
    rows = []
    for code, q in sorted(quotes.items()):
        price, prev = q.get("price"), q.get("prev_close")
        rows.append({
            "股票代號": code,
            "股票名稱": q.get("name") or names.get(code, ""),
            "市場": q.get("market", ""),
            "成交價": price,
            "漲跌(%)": round((price - prev) / prev * 100, 2) if price and prev else None,
            "開盤": q.get("open"), "最高": q.get("high"), "最低": q.get("low"),
            "昨收": prev,
            "總量(張)": q.get("total_vol"),
            "買價": q.get("bid"), "賣價": q.get("ask"),
            "漲停": q.get("limit_up"), "跌停": q.get("limit_down"),
            "時間": q.get("time", ""),
        })
    return pd.DataFrame(rows, columns=QUOTE_COLUMNS)


def parse_kline(result: Any) -> pd.DataFrame:
    """GetKLine -> date/open/high/low/close/volume（成交量單位依元大回傳，台股為張）。"""
    rows = []
    for k in items(field(result, "KLineList")):
        rows.append({
            "date": to_date(field(k, "TimeStamp")),
            "open": num(field(k, "OpenPrice")),
            "high": num(field(k, "HighPrice")),
            "low": num(field(k, "LowPrice")),
            "close": num(field(k, "ClosePrice")),
            "volume": num(field(k, "DealVol"), 0),
        })
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


def snapshot_bars(quotes: Iterable[dict], day: date, lot_size: int = 1000) -> pd.DataFrame:
    """把盤中報價變成「今天」的日 K（成交量換算成股，和資料庫一致）。"""
    rows = []
    for q in quotes:
        price = q.get("price")
        if not price:
            continue
        rows.append({
            "code": q["code"],
            "date": pd.Timestamp(day),
            "open": q.get("open") or price,
            "high": q.get("high") or price,
            "low": q.get("low") or price,
            "close": price,
            "volume": int((q.get("total_vol") or 0) * lot_size),
        })
    return pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume"])
