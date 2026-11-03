"""戰情室裡唯一的元大連線（唯讀：庫存、即時報價、已實現損益；沒有任何下單功能）。

- 整個程式只登入一次，網頁重新整理不會重覆登入（元大有每日登入次數限制）。
- 庫存查詢結果快取 20 秒，報價靠訂閱推播即時更新，不會超過元大的呼叫頻率限制。
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from ..config import Settings
from ..yuanta.client import YuantaClient, YuantaConfig, YuantaError

HOLDINGS_TTL = 20.0
INTRADAY_TTL = 30.0         # 同一檔 1 分 K 最多 30 秒查一次（GetKLine 每秒限 1 次）
RECONNECT_GAP = 600.0       # 自動重連至少隔 10 分鐘
RECONNECT_MAX_PER_DAY = 6   # 每天最多自動重連幾次（元大有每日登入次數限制）
RECONNECT_HOURS = ((7, 30), (21, 30))   # 只在這段時間自動重連（元大清晨維護時段不碰）
MARKET_TTL = 60.0          # 全市場即時快照最多每分鐘更新一次（約 4 次查詢，遠低於元大限制）


def mask(acct: str) -> str:
    return acct[:5] + "*" * max(len(acct) - 5, 0) if acct else ""


class YuantaHub:
    def __init__(self, settings: Settings, factory: Callable[[YuantaConfig], YuantaClient] = YuantaClient):
        self.config = YuantaConfig.from_dict(settings.yuanta or {})
        self._factory = factory
        self.client: YuantaClient | None = None
        self.error: str | None = None
        self.connecting = False
        self.connected_at: str | None = None
        self._lock = threading.Lock()
        self._holdings: pd.DataFrame | None = None
        self._holdings_at = 0.0
        self.holdings_time: str | None = None
        self._market_at = 0.0
        self.market_time: str | None = None
        self._market_busy = threading.Lock()
        self.ever_logged_in = False
        self._intraday: dict[str, tuple[float, list]] = {}
        self._auto_day: str | None = None
        self._auto_count = 0
        self._auto_last = -1e9

    # ---------------------------------------------------------------- 狀態
    @property
    def configured(self) -> bool:
        return bool(self.config.account)

    @property
    def logged_in(self) -> bool:
        return bool(self.client and self.client.logged_in)

    def status(self) -> dict:
        c = self.client
        return {
            "configured": self.configured, "problems": self.config.problems() if self.configured else [],
            "environment": self.config.environment.upper(), "account": mask(self.config.account),
            "logged_in": self.logged_in, "connecting": self.connecting, "error": self.error,
            "connected_at": self.connected_at,
            "subscribed": len(c.subscribed) if c else 0,
            "messages": list(c.messages)[-12:] if c else [],
        }

    # ---------------------------------------------------------------- 連線
    def connect(self) -> dict:
        if not self.configured:
            raise YuantaError("還沒設定元大帳號：請在 config.local.toml 的 [yuanta] 填好帳號、電子密碼與元件資料夾")
        with self._lock:
            if self.logged_in:
                return self.status()
            self.connecting, self.error = True, None
            try:
                if self.client is None:
                    self.client = self._factory(self.config)
                self.client.connect()
                self.connected_at = time.strftime("%H:%M:%S")
                self.ever_logged_in = True
                self._holdings, self._holdings_at = None, 0.0
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                raise YuantaError(self.error) from e
            finally:
                self.connecting = False
        return self.status()

    def auto_reconnect(self, now=None, mono: float | None = None) -> bool:
        """斷線後自動接回（例如元大清晨把連線踢掉）。只在今天登入成功過、白天時段，
        而且每 10 分鐘最多試一次、每天最多 6 次，避免用光登入次數或被判定登入過於頻繁。"""
        from datetime import datetime
        now = now or datetime.now()
        mono = time.monotonic() if mono is None else mono
        c = self.client
        if not self.configured or not self.ever_logged_in or self.logged_in or self.connecting or c is None:
            return False
        if c.cert_error or c.suspended_until > time.monotonic():
            return False
        if now.weekday() >= 5 or not (RECONNECT_HOURS[0] <= (now.hour, now.minute) <= RECONNECT_HOURS[1]):
            return False
        day = now.strftime("%Y-%m-%d")
        if self._auto_day != day:
            self._auto_day, self._auto_count = day, 0
        if self._auto_count >= RECONNECT_MAX_PER_DAY or mono - self._auto_last < RECONNECT_GAP:
            return False
        self._auto_count += 1
        self._auto_last = mono
        c.messages.append(f"{now:%H:%M:%S} 自動重新連線（今天第 {self._auto_count} 次）")
        try:
            self.connect()
        except Exception:  # noqa: BLE001
            pass   # 錯誤訊息存在 self.error，持倉風控頁會顯示
        return True

    def disconnect(self) -> dict:
        with self._lock:
            if self.client is not None:
                self.client.close()
            self._holdings, self._holdings_at = None, 0.0
            self.connected_at = None
        return self.status()

    def _client(self) -> YuantaClient:
        if not self.logged_in:
            raise YuantaError("尚未連線元大")
        return self.client

    # ---------------------------------------------------------------- 帳務
    def holdings(self, force: bool = False) -> pd.DataFrame:
        c = self._client()
        with self._lock:
            if force or self._holdings is None or time.monotonic() - self._holdings_at > HOLDINGS_TTL:
                df = c.holdings()
                self._holdings, self._holdings_at = df, time.monotonic()
                self.holdings_time = time.strftime("%H:%M:%S")
                pairs = [(code, m or "TWSE") for code, m in zip(df["股票代號"], df["市場"])
                         if code not in c.subscribed]
                if pairs:   # 新的持股：先拿一次快照，再訂閱即時推播
                    try:
                        c.snapshot(pairs)
                        c.subscribe(pairs)
                    except YuantaError as e:
                        c.messages.append(f"庫存報價訂閱失敗：{e}")
            return self._holdings

    def quotes(self) -> dict[str, dict]:
        c = self.client
        if c is None:
            return {}
        with c._lock:
            return {k: dict(v) for k, v in c.quotes.items()}

    # ---------------------------------------------------------------- 全市場即時快照
    def refresh_market(self, pairs: list[tuple[str, str]], background: bool = True) -> None:
        """每分鐘最多一次：用元大報價表查詢抓全市場現價（盤中漲跌家數用）。"""
        if not self.logged_in or not pairs or time.monotonic() - self._market_at < MARKET_TTL:
            return
        if not self._market_busy.acquire(blocking=False):
            return

        def run():
            try:
                self.client.snapshot(pairs)
                self._market_at = time.monotonic()
                self.market_time = time.strftime("%H:%M:%S")
            except Exception as e:  # noqa: BLE001
                self._market_at = time.monotonic()   # 失敗也等一分鐘再試，避免狂打元大
                if self.client:
                    self.client.messages.append(f"全市場報價更新失敗：{e}")
            finally:
                self._market_busy.release()
        if background:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    def intraday(self, code: str, market: str) -> dict:
        """今天的分時走勢：元大 1 分 K 為主，最新幾分鐘用即時報價補上。"""
        from datetime import datetime
        c = self._client()
        if code not in c.subscribed:   # 開著這頁時這檔要有即時推播
            try:
                c.snapshot([(code, market)])
                c.subscribe([(code, market)])
            except YuantaError as e:
                c.messages.append(f"{code} 即時報價訂閱失敗：{e}")
        now = time.monotonic()
        hit = self._intraday.get(code)
        kbars, source = (hit[1] if hit else []), "1 分 K"
        if hit is None or now - hit[0] > INTRADAY_TTL:
            try:
                df = c.intraday_kline(code, market, date.today())
                kbars = [(r.time, r.open, r.high, r.low, r.close, r.volume) for r in df.itertuples()]
            except YuantaError as e:
                kbars = []
                c.messages.append(f"{code} 1 分 K 查詢失敗，改用即時報價累積：{e}")
            self._intraday[code] = (now, kbars)
        mine = c.minute_bars(code)
        last_k = kbars[-1][0] if kbars else ""
        bars = list(kbars) + [b for b in mine if b[0] > last_k]
        if not kbars:
            source = "即時報價累積（打開戰情室之後才有）" if bars else ""
        with c._lock:
            q = dict(c.quotes.get(code) or {})
        return {"bars": bars, "quote": q, "source": source, "time": datetime.now().strftime("%H:%M:%S")}

    def realized(self, days: int) -> pd.DataFrame:
        end = date.today()
        return self._client().realized(end - timedelta(days=days), end)
