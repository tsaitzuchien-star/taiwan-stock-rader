"""共用 HTTP：重試、退避、限速，以及錯誤不再被默默吞掉。"""
from __future__ import annotations

import logging
import time

import requests
import urllib3

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


class FetchError(RuntimeError):
    pass


class Http:
    def __init__(self, min_interval: float = 3.0, retries: int = 3, timeout: float = 15.0):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def get_json(self, url: str, params: dict | None = None, allow_insecure_fallback: bool = False):
        """取得 JSON。allow_insecure_fallback 只用在公開行情資料（不送任何帳密），
        而且只有在 SSL 憑證驗證失敗時才退而求其次，並寫進日誌。"""
        last_err: Exception | None = None
        verify = True
        for attempt in range(1, self.retries + 1):
            self._wait()
            try:
                r = self.session.get(url, params=params, timeout=self.timeout, verify=verify)
                if r.status_code != 200:
                    raise FetchError(f"HTTP {r.status_code}")
                return r.json()
            except requests.exceptions.SSLError as e:
                last_err = e
                if allow_insecure_fallback and verify:
                    log.warning("SSL 驗證失敗，改用不驗證模式重試（僅限公開行情）：%s", url)
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    verify = False
                    continue
            except (requests.RequestException, ValueError, FetchError) as e:
                last_err = e
            log.info("第 %d 次請求失敗 %s：%s", attempt, url, last_err)
            time.sleep(2 ** attempt)
        raise FetchError(f"{url} 連續 {self.retries} 次失敗：{last_err}")
