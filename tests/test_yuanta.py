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


def test_no_connection_means_no_login(monkeypatch, tmp_path):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    monkeypatch.setattr("radar.yuanta.client.CONNECT_TIMEOUT", 0.2)
    t = FakeTrader()
    t.Open = lambda env: t.calls.append(("Open", env))  # 主機沒回應
    c, _ = make_client(trader=t, log_dir=str(tmp_path))
    with pytest.raises(YuantaError, match="沒有連上元大主機"):
        c.connect()
    assert not any(x[0] == "Login" for x in t.calls)
    assert c.events_seen == 0 and not c.logged_in


def test_login_retries_when_component_returns_false(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    t = FakeTrader()
    real = t.Login
    answers = [False, True]

    def flaky(acct, pwd):
        if answers.pop(0) is False:
            t.calls.append(("LoginRejected", acct))
            return False
        return real(acct, pwd)
    t.Login = flaky
    c, _ = make_client(trader=t)
    assert c.connect()["ok"] and c.logged_in
    assert [x[0] for x in t.calls if x[0].startswith("Login")] == ["LoginRejected", "Login"]


def test_login_false_every_time_explains_certificate(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    t = FakeTrader()
    t.Login = lambda acct, pwd: (t.calls.append(("Login", acct)), False)[1]
    c, _ = make_client(trader=t)
    with pytest.raises(YuantaError, match="憑證"):
        c.connect()
    assert len([x for x in t.calls if x[0] == "Login"]) == 3 and not c.logged_in


def test_certificate_error_stops_login_retries(monkeypatch):
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    t = FakeTrader()
    c, _ = make_client(trader=t)

    def bad_cert(acct, pwd):
        t.calls.append(("Login", acct))
        c.on_response(1, 9, "", None, "執行異常，憑證檢核失敗:5010")
        return False
    t.Login = bad_cert
    with pytest.raises(YuantaError, match="憑證"):
        c.connect()
    assert len([x for x in t.calls if x[0] == "Login"]) == 1 and not c.logged_in


def test_reconnect_after_server_drops_connection(monkeypatch):
    """元大清晨把連線踢掉後，再連一次要換新的元件物件重新 Open，不能等舊的（舊的永遠不會再連上）。"""
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    monkeypatch.setattr("radar.yuanta.client.CONNECT_TIMEOUT", 1)
    traders = [FakeTrader(), FakeTrader()]
    sdk = fake_sdk(traders[0])
    sdk.Trader = lambda *a: traders.pop(0)
    t1, t2 = traders
    c = YuantaClient(YuantaConfig(account="S98875005091", password="1234", environment="UAT"),
                     sdk=sdk, limiter=RateLimiter(sleep=lambda s: None), timeout=2)
    c.connect()
    c.subscribed.add("2330")
    for idx in (2, 3, 5):   # 05:05 已斷線 / LogOut
        c.on_response(0, idx, "", None, "Disconnected")
    assert not c.logged_in
    info = c.connect()
    assert info["ok"] and c.logged_in
    assert ("Open", "UAT") in t2.calls and [x for x in t2.calls if x[0] == "Login"]
    assert c.subscribed == set()   # 新連線要重新訂閱


def test_hub_auto_reconnect_limits(monkeypatch):
    from datetime import datetime
    from radar.api import yuanta_hub as H
    from radar.config import Settings
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    made = []

    def factory(cfg):
        c, t = make_client()
        made.append(t)
        return c
    hub = H.YuantaHub(Settings(yuanta={"account": "S98875005091", "password": "1", "sdk_dir": "."}), factory=factory)
    monday_9am = datetime(2026, 11, 2, 9, 0)
    assert not hub.auto_reconnect(monday_9am, mono=0)        # 今天還沒成功登入過：不自動登入
    hub.connect()
    assert not hub.auto_reconnect(monday_9am, mono=0)        # 還連著
    hub.client.logged_in = False
    hub.client._connected.clear()
    assert not hub.auto_reconnect(datetime(2026, 11, 2, 5, 10), mono=0)   # 清晨維護時段不碰
    assert not hub.auto_reconnect(datetime(2026, 11, 7, 9, 0), mono=0)    # 週末不碰
    assert hub.auto_reconnect(monday_9am, mono=0) and hub.logged_in
    hub.client.logged_in = False
    assert not hub.auto_reconnect(monday_9am, mono=60)       # 10 分鐘內不再試
    for i in range(1, H.RECONNECT_MAX_PER_DAY):
        hub.client.logged_in = False
        assert hub.auto_reconnect(monday_9am, mono=i * 1000)
    hub.client.logged_in = False
    assert not hub.auto_reconnect(monday_9am, mono=99_999)   # 一天最多 6 次


def test_parse_kline_minutes_and_time():
    from datetime import datetime as DT
    from radar.yuanta.parsers import parse_kline_minutes, to_minute
    r = NS(KLineList=[NS(TimeStamp=DT(2026, 11, 3, 9, 1), OpenPrice=10, HighPrice=11, LowPrice=9.5, ClosePrice=10.5, DealVol=12),
                      NS(TimeStamp="2026/11/03 9:02:00", OpenPrice=10.5, HighPrice=10.6, LowPrice=10.4, ClosePrice=10.6, DealVol=3)])
    df = parse_kline_minutes(r)
    assert list(df["time"]) == ["09:01", "09:02"] and df["close"].tolist() == [10.5, 10.6]
    assert to_minute(NS(Hour=13, Minute=5)) == "13:05" and to_minute("2026/11/03") is None


def test_minute_bars_from_quotes(monkeypatch):
    from datetime import datetime as DT
    c, _ = make_client()
    day = DT.now().replace(hour=9, minute=5)
    with c._lock:
        c._record_minute("2330", {"price": 100.0, "total_vol": 10, "time": "09:05:01"}, now=day)
        c._record_minute("2330", {"price": 102.0, "total_vol": 15, "time": "09:05:40"}, now=day)
        c._record_minute("2330", {"price": 101.0, "total_vol": 18, "time": "09:06:02"}, now=day)
        c._record_minute("2330", {"price": 99.0, "total_vol": 18, "time": "08:59:00"}, now=day)   # 開盤前不記
    bars = c.minute_bars("2330")
    assert bars[0] == ("09:05", 100.0, 102.0, 100.0, 102.0, None)
    assert bars[1] == ("09:06", 101.0, 101.0, 101.0, 101.0, 3)


def test_minute_kline_type_lookup():
    c, _ = make_client()
    c._sdk.KLineType = NS(Day=11, Week=12, Min5=2, Min1=1, Min15=3)
    assert c.minute_kline_type() == 1
    c2, _ = make_client()
    c2._sdk.KLineType = NS(Day=11, Week=12)
    assert c2.minute_kline_type() is None and c2._kline_minute_type is False


def test_hub_intraday_uses_kline_then_live_minutes(monkeypatch):
    from datetime import date as D, datetime as DT
    from radar.api import yuanta_hub as H
    from radar.config import Settings
    monkeypatch.setattr("radar.yuanta.client.time.sleep", lambda s: None)
    t = FakeTrader()
    today = D.today()

    def kline(acct, kind, mkt, code, s, e):
        t.calls.append(("GetKLine", kind))
        t._emit(1, 999, "GetKLine", NS(KLineList=[
            NS(TimeStamp=DT(today.year, today.month, today.day, 9, 1), OpenPrice=10, HighPrice=10, LowPrice=10, ClosePrice=10, DealVol=5)]))
        return True
    t.GetKLine = kline

    def factory(cfg):
        c, _ = make_client(trader=t)
        c._sdk.KLineType = NS(Day=11, Min1=1)
        return c
    hub = H.YuantaHub(Settings(yuanta={"account": "S98875005091", "password": "1", "sdk_dir": "."}), factory=factory)
    hub.connect()
    with hub.client._lock:
        hub.client._record_minute("2330", {"price": 10.5, "total_vol": 9, "time": "09:02:00"}, now=DT.now())
    r = hub.intraday("2330", "TWSE")
    assert [b[0] for b in r["bars"]] == ["09:01", "09:02"] and r["source"] == "1 分 K"
    assert "2330" in hub.client.subscribed
    hub.intraday("2330", "TWSE")
    assert sum(1 for x in t.calls if x[0] == "GetKLine") == 1   # 30 秒內不重查
