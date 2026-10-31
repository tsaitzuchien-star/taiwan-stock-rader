"""元大 Spark API 唯讀用戶端：登入、庫存、損益、報價、K 線。

刻意「不」提供任何下單、改單、刪單、條件單功能。

元大 API 是非同步的：呼叫函式後，結果會從 OnResponse 事件回來（strIndex = 函式名稱）。
這裡把它包成「呼叫 -> 等回應 -> 回傳結果」的同步介面，並內建元大的安控限制：
  - 同一功能每秒最多 3 次（GetKLine 每秒 1 次）→ 這裡保守用 2.5 次 / 0.9 次
  - 單次訂閱最多 200 檔、所有訂閱合計最多 2000 檔 → 這裡上限 1900
  - 報價表查詢 (GetWatchListAll) 單次最多 600 檔 → 這裡每批 500
  - 超過限制會被暫停 1 分鐘，1 小時內 10 次會停權 → 收到暫停通知就全部停手
  - 登入失敗後至少隔 4 秒才能再登入；已登入不可重覆登入
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pandas as pd

from . import parsers

log = logging.getLogger(__name__)

QUERY_INTERVAL = 0.4       # 秒；同一功能每秒最多 3 次
KLINE_INTERVAL = 1.1       # 秒；GetKLine 每秒 1 次
SUBSCRIBE_BATCH = 200
SNAPSHOT_BATCH = 500
MAX_SUBSCRIPTIONS = 1900
LOGIN_RETRY_GAP = 5.0
CONNECT_TIMEOUT = 20
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "yuanta"
SUSPEND_SECONDS = 65
# 元大元件資料夾裡一定要有的檔案（YuantaSparkAPI.dll 依賴其他 dll，不能單獨複製）
SDK_REQUIRED_FILES = ("YuantaSparkAPI.dll", "YuantaSparkAPI.deps.json", "NX_Login_Model.dll",
                      "Nexus.TCBus.StandardModel.dll", "NX_PositionPortfolio_Model.dll")

# OnResponse 的 dwIndex（intMark=1 時）錯誤代碼
QUERY_ERRORS = {3: "尚未登入", 4: "登入帳號錯誤", 5: "功能代碼錯誤或權限不足",
                6: "訂閱即時回報失敗", 7: "SocketRPRead 失敗", 9: "加簽失敗 / 憑證異常",
                10: "已登出", 11: "帳號資訊異常", 12: "取得已訂閱商品清單異常",
                13: "觸發使用限制（暫停 1 分鐘）", 14: "觸發停權"}
SYSTEM_EVENTS = {1: "已連線", 2: "已斷線", 3: "網路異常", 4: "需要下載新版 API",
                 5: "尚未連線", 6: "系統公告"}


class YuantaError(Exception):
    pass


@dataclass
class YuantaConfig:
    account: str = ""
    password: str = ""
    environment: str = "PROD"           # PROD 正式 / UAT 測試
    sdk_dir: str = ""                    # YuantaSparkAPI.dll 所在資料夾
    cert_path: str = ""                  # 選填：pfx 憑證路徑（Windows 預設用系統憑證存放區）
    cert_password: str = ""
    log_dir: str = ""                    # 元大元件 log 存放處（預設：專案資料夾 logs/yuanta）
    pmm_check: bool = True               # 元大 SetPMMServerCheck，連不上時可試著設成 false

    @classmethod
    def from_dict(cls, d: dict) -> "YuantaConfig":
        known = {}
        for k, v in d.items():
            if k not in cls.__dataclass_fields__:
                continue
            if k == "pmm_check":
                known[k] = v if isinstance(v, bool) else str(v).strip().lower() not in ("false", "0", "no")
                continue
            v = str(v)
            if k in ("sdk_dir", "cert_path", "log_dir"):
                v = v.strip().strip("\"'").strip()   # 貼路徑時常多帶空白或引號
            elif k in ("account", "environment"):
                v = v.strip()
            known[k] = v
        return cls(**known)

    def problems(self) -> list[str]:
        out = []
        if not self.account:
            out.append("config.local.toml 的 [yuanta] 沒有填 account")
        elif not (self.account[:1].upper() == "S" and len(self.account) == 12):
            out.append("account 格式應為 S + 11 碼（分公司 4 碼 + 帳號 7 碼）")
        if not self.password:
            out.append("config.local.toml 的 [yuanta] 沒有填 password（電子密碼）")
        if self.environment.upper() not in ("PROD", "UAT"):
            out.append("environment 只能是 PROD 或 UAT")
        if not self.sdk_dir:
            out.append("config.local.toml 的 [yuanta] 沒有填 sdk_dir（YuantaSparkAPI.dll 所在資料夾）")
        elif missing_sdk_files(self.sdk_dir):
            out.append(f"{self.sdk_dir} 不是完整的元大元件資料夾，缺少 "
                       f"{'、'.join(missing_sdk_files(self.sdk_dir))}"
                       "（請指到放元大範例程式 YSendOrder.py 的資料夾）")
        return out


# ---------------------------------------------------------------- 載入 .NET 元件
def load_sdk(sdk_dir: str | Path) -> SimpleNamespace:
    """依元大官方說明載入 .NET 8 與 YuantaSparkAPI.dll，回傳需要用到的型別。"""
    sdk_dir = Path(sdk_dir).resolve()
    missing = missing_sdk_files(sdk_dir)
    if missing:
        raise YuantaError(f"{sdk_dir} 不是完整的元大元件資料夾，缺少：{'、'.join(missing)}。"
                          "請把 sdk_dir 指到放元大範例程式 YSendOrder.py 的那個資料夾")
    if sys.platform == "win32":
        os.add_dll_directory(str(sdk_dir))
    if str(sdk_dir) not in sys.path:
        sys.path.append(str(sdk_dir))

    try:
        import pythonnet
    except ImportError as e:
        raise YuantaError("缺少 pythonnet 套件，請重新執行 啟動雷達.bat 安裝") from e

    if pythonnet.get_runtime_info() is None:
        errors = []
        # 1) 官方做法：使用電腦上安裝的 .NET 8
        try:
            pythonnet.load("coreclr")
        except Exception as e:  # noqa: BLE001
            errors.append(f"系統 .NET：{e}")
            # 2) 退而求其次：使用元大資料夾內附的 .NET 執行環境
            try:
                pythonnet.set_runtime("coreclr", runtime_config=str(sdk_dir / "YuantaSparkAPI.runtimeconfig.json"),
                                      dotnet_root=str(sdk_dir))
                pythonnet.load()
            except Exception as e2:  # noqa: BLE001
                errors.append(f"元大內附 .NET：{e2}")
                raise YuantaError(
                    "無法啟動 .NET 8。請到 https://dotnet.microsoft.com/download/dotnet/8.0 "
                    "安裝「.NET Runtime 8」（Windows x64），裝好後重新開機再試。\n" + "\n".join(errors))

    import clr  # type: ignore
    clr.AddReference("System.Collections")
    dll = sdk_dir / "YuantaSparkAPI.dll"
    try:
        asm = clr.AddReference(str(dll))  # 用完整路徑，確保載入的是這個資料夾的元件
    except Exception as e:  # noqa: BLE001
        raise YuantaError(f"載入 {dll} 失敗：{e}") from e

    from System.Collections.Generic import List  # type: ignore
    try:
        from YuantaOneAPI import (  # type: ignore
            YuantaSparkAPITrader, OnResponseEventHandler, enumEnvironmentMode, enumLogType,
            enumMarketType, WatchlistAll, Quote, KLineType)
    except ImportError as e:
        raise YuantaError("YuantaSparkAPI.dll 載入了，但讀不到裡面的功能。"
                          + _type_load_problem(asm)) from e
    return SimpleNamespace(
        List=List,
        Trader=YuantaSparkAPITrader,
        Handler=OnResponseEventHandler,
        Env=enumEnvironmentMode,
        LogType=enumLogType,
        Market=enumMarketType,
        WatchlistAll=WatchlistAll,
        Quote=Quote,
        KLineType=KLineType,
    )


def missing_sdk_files(sdk_dir: str | Path) -> list[str]:
    """元大元件要整個資料夾一起用；只複製 YuantaSparkAPI.dll 會缺相依檔。"""
    return [f for f in SDK_REQUIRED_FILES if not (Path(sdk_dir) / f).exists()]


def _type_load_problem(asm) -> str:
    """找出 .NET 讀不到型別的真正原因（通常是缺少相依的 dll）。"""
    try:
        asm.GetTypes()
        return f"（元件位置：{asm.Location}）"
    except Exception as e:  # noqa: BLE001
        inner = getattr(e, "LoaderExceptions", None) or getattr(getattr(e, "InnerException", None),
                                                                "LoaderExceptions", None)
        msgs = sorted({str(x.Message) for x in inner if x is not None}) if inner else [str(e)]
        return "原因：\n  " + "\n  ".join(msgs[:8])


# ---------------------------------------------------------------- 速率限制
class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._clock, self._sleep = clock, sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, interval: float) -> None:
        with self._lock:
            now = self._clock()
            due = self._last.get(key, -1e9) + interval
            if due > now:
                self._sleep(due - now)
                now = due
            self._last[key] = now


def chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


@dataclass
class _Pending:
    event: threading.Event = dc_field(default_factory=threading.Event)
    value: Any = None
    error: str | None = None


# ---------------------------------------------------------------- 用戶端
class YuantaClient:
    """唯讀用戶端。每個 Streamlit 程序只建立一個（st.cache_resource）。"""

    def __init__(self, config: YuantaConfig, sdk: SimpleNamespace | None = None,
                 limiter: RateLimiter | None = None, timeout: float = 15.0):
        self.config = config
        self._sdk = sdk
        self._limiter = limiter or RateLimiter()
        self._timeout = timeout
        self._api = None
        self._lock = threading.RLock()          # 保護 quotes（推播執行緒會寫入）
        self._connect_lock = threading.Lock()   # 連線 / 登入 / 關閉
        self._fn_locks: dict[str, threading.Lock] = {}
        self._pending: dict[str, _Pending] = {}
        self._connected = threading.Event()
        self.logged_in = False
        self.login_info: dict = {}
        self.quotes: dict[str, dict] = {}
        self.subscribed: set[str] = set()
        self.messages: deque[str] = deque(maxlen=200)
        self.reports: deque[str] = deque(maxlen=200)
        self.suspended_until = 0.0
        self.events_seen = 0                    # 收到幾個元大事件（診斷用）
        self._handler = None                    # 保留 .NET 委派的參考，避免被回收
        self.log_dir: Path | None = None
        self._last_login_fail = 0.0

    # ---------- 事件 ----------
    def _note(self, text: str) -> None:
        self.messages.append(f"{datetime.now():%H:%M:%S} {text}")
        log.info(text)

    def on_response(self, intMark, dwIndex, strIndex, objHandle, objValue) -> None:  # noqa: N803
        """元大 OnResponse 事件（在 .NET 執行緒呼叫）。任何例外都要吃掉，不能讓元件掛掉。"""
        try:
            self.events_seen += 1
            mark, idx, name = int(intMark), int(dwIndex), str(strIndex or "")
            if mark == 0:
                self._on_system(idx, objValue)
            elif mark == 1:
                self._on_query(idx, name, objValue)
            elif mark == 2:
                self._on_push(idx, name, objValue)
        except Exception as e:  # noqa: BLE001
            log.exception("處理元大回應失敗")
            self.messages.append(f"處理回應失敗：{e}")

    def _on_system(self, idx: int, value: Any) -> None:
        self._note(f"系統：{SYSTEM_EVENTS.get(idx, idx)} {value}")
        if idx == 1:
            self._connected.set()
        elif idx in (2, 3, 5):
            self._connected.clear()
            self.logged_in = False
            self._fail_all(f"連線中斷：{SYSTEM_EVENTS.get(idx)}")

    def _on_query(self, idx: int, name: str, value: Any) -> None:
        if idx in QUERY_ERRORS:
            text = f"{QUERY_ERRORS[idx]}：{value}"
            self._note(text)
            if idx == 13:
                self.suspended_until = time.monotonic() + SUSPEND_SECONDS
            if idx == 14:
                self.suspended_until = float("inf")
            if idx == 10:
                self.logged_in = False
            if name in self._pending:
                self._finish(name, error=text)
            elif idx in (3, 4, 9, 10, 11, 13, 14):
                self._fail_all(text)
            return
        if name in self._pending:
            self._finish(name, value=value)
        elif not name:
            self._note(f"回應：{value}")

    def _on_push(self, idx: int, name: str, value: Any) -> None:
        if name == "SubscribeWatchlistAll":
            with self._lock:
                parsers.apply_watchlist_push(self.quotes, value)
        elif name.startswith("RR_"):
            self.reports.append(f"{datetime.now():%H:%M:%S} {name} {value}")
        elif idx == 1:
            self._note(f"訂閱失敗：{name} {value}")

    def _finish(self, name: str, value: Any = None, error: str | None = None) -> None:
        p = self._pending.get(name)
        if p:
            p.value, p.error = value, error
            p.event.set()

    def _fail_all(self, error: str) -> None:
        for name in list(self._pending):
            self._finish(name, error=error)

    # ---------- 呼叫 ----------
    def _check_ready(self, need_login: bool = True) -> None:
        if self.suspended_until > time.monotonic():
            if self.suspended_until == float("inf"):
                raise YuantaError("元大通知已停權，請聯絡營業員。程式已停止所有呼叫。")
            left = int(self.suspended_until - time.monotonic())
            raise YuantaError(f"元大暫停呼叫中（超過使用限制），約 {left} 秒後再試")
        if self._api is None:
            raise YuantaError("尚未連線元大")
        if need_login and not self.logged_in:
            raise YuantaError("尚未登入元大")

    def _request(self, name: str, invoke: Callable[[], Any], interval: float = QUERY_INTERVAL,
                 need_login: bool = True) -> Any:
        lock = self._fn_locks.setdefault(name, threading.Lock())
        with lock:
            self._check_ready(need_login)
            self._limiter.wait(name, interval)
            pending = self._pending[name] = _Pending()
            try:
                accepted = invoke()
                if accepted is False:
                    raise YuantaError(f"{name} 送出失敗（元件回傳 False）")
                if not pending.event.wait(self._timeout):
                    raise YuantaError(f"{name} 等候回應逾時（{self._timeout:.0f} 秒）")
                if pending.error:
                    raise YuantaError(f"{name}：{pending.error}")
                return pending.value
            finally:
                self._pending.pop(name, None)

    def _market_enum(self, market: str):
        return self._sdk.Market.TWOTC if market == "TPEX" else self._sdk.Market.TWSE

    def _list(self, typ, objs: list):
        lst = self._sdk.List[typ]()
        for o in objs:
            lst.Add(o)
        return lst

    # ---------- 連線 / 登入 ----------
    def connect(self) -> dict:
        """載入元件、連線、登入。已登入就直接回傳。"""
        with self._connect_lock:
            if self.logged_in:
                return self.login_info
            problems = self.config.problems() if self._sdk is None else []
            if problems:
                raise YuantaError("；".join(problems))
            if self._sdk is None:
                self._sdk = load_sdk(self.config.sdk_dir)
            if self._api is None:
                log_dir = Path(self.config.log_dir) if self.config.log_dir else DEFAULT_LOG_DIR
                log_dir.mkdir(parents=True, exist_ok=True)
                self.log_dir = log_dir
                api = self._sdk.Trader(str(log_dir))
                self._handler = self._sdk.Handler(self.on_response)
                api.OnResponse += self._handler
                api.SetLogType(self._sdk.LogType.COMMON)
                if not self.config.pmm_check:
                    api.SetPMMServerCheck(False)
                env = self._sdk.Env.UAT if self.config.environment.upper() == "UAT" else self._sdk.Env.PROD
                self._api = api
                self._note(f"開啟連線（{self.config.environment.upper()}）…")
                api.Open(env)
            if not self._connected.wait(CONNECT_TIMEOUT):
                # 沒連上就不送登入：元大有「每日登入次數」限制，不要白白消耗
                api, self._api = self._api, None
                try:
                    api.Close()
                    api.Dispose()
                except Exception:  # noqa: BLE001
                    pass
                raise YuantaError(
                    f"{CONNECT_TIMEOUT} 秒內沒有連上元大主機（收到 {self.events_seen} 個元大事件），所以沒有送出登入。\n"
                    "常見原因：\n"
                    "  1. 元大主機在非服務時間（週末、深夜）維護，請在平日白天再試\n"
                    "  2. 防毒軟體或防火牆擋住了連線\n"
                    "  3. 元大憑證服務（YuantaServiSign）沒有在執行\n"
                    f"元大元件的紀錄檔在：{self.log_dir}")
            gap = time.monotonic() - self._last_login_fail
            if gap < LOGIN_RETRY_GAP:
                time.sleep(LOGIN_RETRY_GAP - gap)
            c = self.config
            if c.cert_path:
                invoke = lambda: self._api.Login(c.cert_path, c.cert_password, c.account, c.password)  # noqa: E731
            else:
                invoke = lambda: self._api.Login(c.account, c.password)  # noqa: E731
            try:
                result = self._request("Login", invoke, interval=0, need_login=False)
            except YuantaError:
                self._last_login_fail = time.monotonic()
                raise
            info = parsers.parse_login(result)
            if not info["ok"]:
                self._last_login_fail = time.monotonic()
                raise YuantaError(f"登入失敗（{info['code']}）{info['message']}")
            self.logged_in = True
            self.login_info = info
            self._note(f"登入成功：{info['message']}")
            time.sleep(1)  # 元大範例：登入後稍等再呼叫其他功能
            return info

    def close(self) -> None:
        with self._connect_lock:
            if self._api is None:
                return
            try:
                if self.subscribed:
                    self.unsubscribe_all()
                self._api.LogOut()
                self._api.Close()
                self._api.Dispose()
            except Exception as e:  # noqa: BLE001
                log.warning("關閉元大連線時發生錯誤：%s", e)
            self._api = None
            self.logged_in = False
            self._connected.clear()

    @property
    def account(self) -> str:
        return self.config.account

    # ---------- 帳務（唯讀） ----------
    def holdings(self) -> pd.DataFrame:
        """股票庫存綜合總表（含市價、未實現損益）。"""
        r = self._request("GetStoreSummary", lambda: self._api.GetStoreSummary(self.account))
        return parsers.parse_store_summary(r)

    def unrealized_detail(self, code: str, market: str = "TWSE") -> pd.DataFrame:
        r = self._request("GetUnrealizedGainLossDetail",
                          lambda: self._api.GetUnrealizedGainLossDetail(
                              self.account, self._market_enum(market), code))
        return parsers.parse_unrealized_detail(r)

    def realized(self, start: date, end: date) -> pd.DataFrame:
        r = self._request("GetHisRealizedGainLoss",
                          lambda: self._api.GetHisRealizedGainLoss(
                              self.account, start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")))
        return parsers.parse_realized(r)

    # ---------- 行情 ----------
    def _quote_objs(self, typ, codes: list[tuple[str, str]]):
        objs = []
        for code, market in codes:
            o = typ()
            o.MarketType = self._market_enum(market)
            o.StockCode = code
            objs.append(o)
        return objs

    def snapshot(self, codes: list[tuple[str, str]],
                 progress: Callable[[float], None] | None = None) -> dict[str, dict]:
        """報價表查詢：一次拿到多檔的現價、開高低、昨收、總量。codes = [(代號, 'TWSE'/'TPEX')]"""
        out: dict[str, dict] = {}
        batches = list(chunks(list(codes), SNAPSHOT_BATCH))
        for i, batch in enumerate(batches):
            lst = self._list(self._sdk.Quote, self._quote_objs(self._sdk.Quote, batch))
            r = self._request("GetWatchListAll", lambda: self._api.GetWatchListAll(self.account, lst))
            out.update(parsers.parse_watchlist_snapshot(r))
            if progress:
                progress((i + 1) / len(batches))
        with self._lock:
            for code, q in out.items():
                self.quotes.setdefault(code, {}).update(q)
        return out

    def subscribe(self, codes: list[tuple[str, str]]) -> int:
        """訂閱即時報價（成交價、買賣價、總量會自動更新到 self.quotes）。回傳新增檔數。"""
        new = [(c, m) for c, m in codes if c not in self.subscribed]
        room = MAX_SUBSCRIPTIONS - len(self.subscribed)
        if len(new) > room:
            self._note(f"訂閱上限 {MAX_SUBSCRIPTIONS} 檔，只訂閱前 {room} 檔")
            new = new[:max(room, 0)]
        for batch in chunks(new, SUBSCRIBE_BATCH):
            self._check_ready()
            self._limiter.wait("SubscribeWatchlistAll", QUERY_INTERVAL)
            lst = self._list(self._sdk.WatchlistAll, self._quote_objs(self._sdk.WatchlistAll, batch))
            if self._api.SubscribeWatchlistAll(self.account, lst) is False:
                raise YuantaError("訂閱即時報價失敗")
            self.subscribed.update(c for c, _ in batch)
        return len(new)

    def unsubscribe_all(self) -> None:
        codes = sorted(self.subscribed)
        markets = {c: self.quotes.get(c, {}).get("market", "TWSE") for c in codes}
        for batch in chunks(codes, SUBSCRIBE_BATCH):
            self._limiter.wait("UnSubscribeWatchlistAll", QUERY_INTERVAL)
            lst = self._list(self._sdk.WatchlistAll,
                             self._quote_objs(self._sdk.WatchlistAll, [(c, markets[c]) for c in batch]))
            self._api.UnSubscribeWatchlistAll(self.account, lst)
            self.subscribed.difference_update(batch)

    def quote_table(self, codes: Iterable[str] | None = None,
                    names: dict[str, str] | None = None) -> pd.DataFrame:
        with self._lock:
            data = {c: dict(q) for c, q in self.quotes.items() if codes is None or c in set(codes)}
        return parsers.quotes_frame(data, names)

    def kline(self, code: str, market: str, start: date, end: date, kind: int = 11) -> pd.DataFrame:
        """K 線查詢（11 日 K / 12 週 K / 13 月 K）。日 K 單次最多 1 年。"""
        r = self._request("GetKLine", lambda: self._api.GetKLine(
            self.account, self._sdk.KLineType(kind), self._market_enum(market), code,
            start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")), interval=KLINE_INTERVAL)
        return parsers.parse_kline(r)
