"""元大串接的單元測試：用假的 .NET 物件模擬元大回應（不需要 DLL）。"""
from datetime import date
from types import SimpleNamespace as NS
import threading

import pandas as pd
import pytest

from radar.indicators import compute_metrics, merge_intraday
from radar.yuanta import parsers
from radar.yuanta.client import RateLimiter, YuantaClient, YuantaConfig, YuantaError


# ---------------- parsers ----------------
def test_parse_login_success_and_failure():
    ok = NS(LoginStatus=NS(MsgCode="0001", MsgContent="登入成功", Count=1),
            LoginList=[NS(Account="S98875005091")])
    assert parsers.parse_login(ok) == {"ok": True, "code": "0001", "message": "登入成功",
                                       "accounts": ["S98875005091"]}
    bad = NS(LoginStatus=NS(MsgCode="0102", MsgContent="密碼凍結或未啟用", Count=0), LoginList=[])
    assert parsers.parse_login(bad)["ok"] is False


def test_parse_store_summary_skips_empty_and_computes_return():
    result = NS(StkStoreList=[
        NS(StkCode="2330", StkName="台積電", MarketNo="TWSE", TradeKind=0, StockQty=1000,
           TradingQty=1000, Price=500.0, Cost=500713.0, MarketPrice=600.0, MarketAmt=600000.0,
           ReturnAmt=97000.0),
        NS(StkCode="6488", StkName="環球晶", MarketNo="TWOTC", TradeKind=3, StockQty=0,
           TradingQty=0, Price=0, Cost=0, MarketPrice=0, MarketAmt=0, ReturnAmt=0),
    ], OVStkStoreList=[])
    df = parsers.parse_store_summary(result)
    assert list(df["股票代號"]) == ["2330"]
    row = df.iloc[0]
    assert row["市場"] == "TWSE" and row["交易種類"] == "現股"
    assert row["報酬率(%)"] == round(97000 / 500713 * 100, 2)


def test_watchlist_snapshot_ignores_market_order_prices():
    r = NS(QueryWatchList=[NS(StkCode="2330", StkName="台積電", MarketNo="TWSE", DealPrice=600.0,
                              OpenPrice=595.0, HighPrice=605.0, LowPrice=590.0, YstPrice=590.0,
                              OpenRefPrice=590.0, UpStopPrice=649.0, DownStopPrice=531.0,
                              BuyPrice=999999999.0, SellPrice=600.0, TotalVol=25000,
                              Time=NS(Hour=10, Minute=5, Second=3))])
    q = parsers.parse_watchlist_snapshot(r)["2330"]
    assert q["price"] == 600.0 and q["bid"] is None and q["ask"] == 600.0
    assert q["time"] == "10:05:03" and q["market"] == "TWSE"


def test_watchlist_push_updates_quote():
    quotes = {"2330": {"code": "2330", "price": 600.0, "open": 595.0, "high": 605.0, "low": 590.0}}
    t = NS(bytHour=13, bytMin=1, bytSec=2, ushtMSec=0)
    parsers.apply_watchlist_push(quotes, NS(StkCode="2330", MarketType="TWSE", IndexFlag=29,
                                            IndexFlag_29=NS(Deal=610.0, TotalVol=30000, Time=t)))
    parsers.apply_watchlist_push(quotes, NS(StkCode="2330", MarketType="TWSE", IndexFlag=28,
                                            IndexFlag_28=NS(BuyPrice=609.0, SellPrice=610.0)))
    parsers.apply_watchlist_push(quotes, NS(StkCode="2330", MarketType="TWSE", IndexFlag=201, Value=590.0))
    q = quotes["2330"]
    assert (q["price"], q["high"], q["total_vol"], q["time"]) == (610.0, 610.0, 30000, "13:01:02")
    assert (q["bid"], q["ask"], q["prev_close"]) == (609.0, 610.0, 590.0)
    df = parsers.quotes_frame(quotes, {"2330": "台積電"})
    assert df.iloc[0]["股票名稱"] == "台積電" and df.iloc[0]["漲跌(%)"] == round(20 / 590 * 100, 2)


def test_parse_kline_dates():
    r = NS(KLineList=[NS(TimeStamp=NS(Year=2026, Month=10, Day=30), OpenPrice=1, HighPrice=2,
                         LowPrice=0.5, ClosePrice=1.5, DealVol=100)])
    df = parsers.parse_kline(r)
    assert df.iloc[0]["date"] == date(2026, 10, 30) and df.iloc[0]["volume"] == 100


def test_merge_intraday_replaces_today_and_feeds_metrics():
    days = pd.bdate_range("2026-07-01", periods=70)
    bars = pd.DataFrame([{"code": "1101", "date": d, "open": 10.0, "high": 10.5, "low": 9.5,
                          "close": 10.0, "volume": 1_000_000} for d in days])
    today = days[-1] + pd.offsets.BDay(1)
    snap = parsers.snapshot_bars([{"code": "1101", "price": 11.0, "open": 10.2, "high": 11.0,
                                   "low": 10.1, "total_vol": 5000}], today.date())
    merged = merge_intraday(bars, snap)
    assert len(merged) == 71 and merged.iloc[-1]["volume"] == 5_000_000
    again = merge_intraday(merged, snap)
    assert len(again) == 71
    df, missing = compute_metrics(merged, {"1101": "台泥"})
    assert not missing and df.iloc[0]["現價(元)"] == 11.0 and df.iloc[0]["今日成交(張)"] == 5000


# ---------------- client（假元件） ----------------
class FakeList(list):
    def Add(self, x):
        self.append(x)


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self


class FakeTrader:
    """模擬元大元件：呼叫後在另一個執行緒觸發 OnResponse，就像真的元件一樣。"""

    def __init__(self, *a):
        self.OnResponse = FakeEvent()
        self.calls = []
        self.login_code = "0001"

    def _emit(self, mark, idx, name, value):
        def fire():
            for h in self.OnResponse.handlers:
                h(mark, idx, name, None, value)
        threading.Timer(0.01, fire).start()

    def SetLogType(self, t): pass

    def Open(self, env):
        self.calls.append(("Open", env))
        self._emit(0, 1, "", "Connect")

    def Login(self, acct, pwd):
        self.calls.append(("Login", acct))
        self._emit(1, 0, "Login", NS(LoginStatus=NS(MsgCode=self.login_code, MsgContent="訊息", Count=1),
                                    LoginList=[NS(Account=acct)]))
        return True

    def GetStoreSummary(self, acct):
        self.calls.append(("GetStoreSummary", acct))
        self._emit(1, 999, "GetStoreSummary", NS(StkStoreList=[], OVStkStoreList=[]))
        return True

    def GetWatchListAll(self, acct, lst):
        self.calls.append(("GetWatchListAll", len(lst)))
        self._emit(1, 999, "GetWatchListAll", NS(QueryWatchList=[
            NS(StkCode=q.StockCode, MarketNo=q.MarketType, DealPrice=10.0) for q in lst]))
        return True

    def SubscribeWatchlistAll(self, acct, lst):
        self.calls.append(("Subscribe", len(lst)))
        return True

    def LogOut(self): self.calls.append(("LogOut",))
    def Close(self): pass
    def Dispose(self): pass


class _Obj:
    pass


class ListFactory:
    def __getitem__(self, typ):
        return FakeList


def fake_sdk(trader):
    return NS(List=ListFactory(), Trader=lambda *a: trader,
              Handler=lambda f: f, Env=NS(UAT="UAT", PROD="PROD"), LogType=NS(COMMON=0),
              Market=NS(TWSE="TWSE", TWOTC="TWOTC"), WatchlistAll=_Obj, Quote=_Obj, KLineType=int)


def make_client(trader=None, **cfg):
    trader = trader or FakeTrader()
    sdk = fake_sdk(trader)
    no_sleep = RateLimiter(sleep=lambda s: None)
    c = YuantaClient(YuantaConfig(account="S98875005091", password="1234", environment="UAT", **cfg),
                     sdk=sdk, limiter=no_sleep, timeout=2)
    return c, trader


def test_client_login_and_queries(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    c, t = make_client()
    info = c.connect()
    assert info["ok"] and c.logged_in
    assert c.connect() is info  # 已登入不可重覆登入
    assert [x for x in t.calls if x[0] == "Login"] == [("Login", "S98875005091")]
    assert c.holdings().empty
    codes = [(f"{i:04d}", "TPEX" if i % 2 else "TWSE") for i in range(1200)]
    snap = c.snapshot(codes)
    assert len(snap) == 1200
    assert [n for name, n in t.calls if name == "GetWatchListAll"] == [500, 500, 200]
    assert snap["0001"]["market"] == "TPEX"


def test_subscribe_respects_batch_and_total_cap(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    c, t = make_client()
    c.connect()
    added = c.subscribe([(f"{i:04d}", "TWSE") for i in range(2500)])
    assert added == 1900
    assert max(n for name, n in t.calls if name == "Subscribe") == 200
    assert c.subscribe([("0001", "TWSE")]) == 0


def test_login_failure_raises(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    t = FakeTrader()
    t.login_code = "0102"
    c, _ = make_client(trader=t)
    with pytest.raises(YuantaError, match="登入失敗"):
        c.connect()
    assert not c.logged_in


def test_suspension_blocks_calls(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    c, _ = make_client()
    c.connect()
    c.on_response(1, 13, "", None, "使用限制觸發")
    with pytest.raises(YuantaError, match="暫停"):
        c.holdings()


def test_client_has_no_order_functions():
    names = [n.lower() for n in dir(YuantaClient)]
    assert not any(w in n for n in names for w in ("order", "sendstock", "strategy"))


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept = []
    rl = RateLimiter(clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    rl.wait("GetKLine", 1.1)
    rl.wait("GetKLine", 1.1)
    rl.wait("Other", 1.1)
    assert slept == [pytest.approx(1.1)]


def test_config_problems(tmp_path):
    cfg = YuantaConfig(account="123", password="", sdk_dir=str(tmp_path))
    msgs = " ".join(cfg.problems())
    assert "S + 11 碼" in msgs and "password" in msgs and "YuantaSparkAPI.dll" in msgs and "YSendOrder.py" in msgs
