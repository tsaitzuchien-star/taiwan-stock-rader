from datetime import date, timedelta

import numpy as np
import pandas as pd

from radar import collect
from radar.collectors.exchanges import NoData, Quote
from radar.collectors.http import FetchError
from radar.config import Settings
from radar.indicators import compute_metrics, stock_metrics, to_ohlcv
from radar.storage import Store

HOLIDAY = date(2026, 10, 9)  # 國慶日補假（10/10 為週六）


def fake_fetcher(market, codes, fail_days=()):
    def fetch(http, day):
        if day == HOLIDAY:
            raise NoData("休市")
        if day in fail_days:
            raise FetchError("HTTP 503")
        base = (day - date(2026, 1, 1)).days
        return [Quote(c, f"名{c}", base + 1.0, base + 2.0, base + 0.5, base + 1.5, 1_000_000) for c in codes]
    return fetch


def make_store_and_collect(today, days=70, fail_days=()):
    store = Store(":memory:")
    settings = Settings(history_trading_days=days, request_interval_sec=0)
    fetchers = {"TWSE": fake_fetcher("TWSE", ["2330", "2317"], fail_days),
                "TPEX": fake_fetcher("TPEX", ["6488"])}
    report = collect.update(store, settings, http=object(), today=today, fetchers=fetchers)
    return store, settings, fetchers, report


def test_collect_backfills_then_only_fetches_new_days():
    today = date(2026, 10, 30)
    store, settings, fetchers, report = make_store_and_collect(today)
    assert report.fetched == {"TWSE": 70, "TPEX": 70}
    assert store.fetch_status("TWSE", HOLIDAY) == "holiday"
    assert len(store.trading_days()) == 70
    stocks = store.stocks()
    assert set(stocks.loc[stocks.market == "TPEX", "code"]) == {"6488"}

    nxt = today + timedelta(days=3)  # 下週一
    report2 = collect.update(store, settings, http=object(), today=nxt, fetchers=fetchers)
    assert report2.fetched == {"TWSE": 1, "TPEX": 1}
    assert len(store.trading_days()) == 70  # 舊資料被修剪
    assert store.trading_days()[-1] == nxt.isoformat()


def test_collect_records_errors_and_retries_them_later():
    today = date(2026, 10, 30)
    bad = date(2026, 10, 29)
    store, settings, fetchers, report = make_store_and_collect(today, fail_days={bad})
    assert store.fetch_status("TWSE", bad) == "error"
    assert any("2026-10-29" in e for e in report.errors)
    fetchers["TWSE"] = fake_fetcher("TWSE", ["2330", "2317"])
    report2 = collect.update(store, settings, http=object(), today=today, fetchers=fetchers)
    assert store.fetch_status("TWSE", bad) == "ok"
    assert not report2.errors


def test_today_without_data_is_not_marked_holiday():
    today = date(2026, 10, 30)
    store = Store(":memory:")
    settings = Settings(history_trading_days=5, request_interval_sec=0)

    def fetch(http, day):
        if day == today:
            raise NoData("尚未公布")
        return [Quote("2330", "台積電", 1, 1, 1, 1, 1)]
    collect.update(store, settings, http=object(), today=today, fetchers={"TWSE": fetch})
    assert store.fetch_status("TWSE", today) == "nodata"


def legacy_metrics(df_history):
    """V10.9.7 原始計算（節錄自 app.py execute_radar_scan），用來對照新版結果。"""
    latest = df_history.iloc[-1]; yesterday = df_history.iloc[-2]
    open_p = float(latest['Open']); close_p = float(latest['Close'])
    high_p = float(latest['High']); low_p = float(latest['Low'])
    today_vol = float(latest['Volume']) / 1000
    yesterday_close = float(yesterday['Close'])
    vol5 = float(df_history['Volume'].tail(5).mean() / 1000)
    vol20 = float(df_history['Volume'].tail(20).mean() / 1000)
    ma20 = float(df_history['Close'].tail(20).mean())
    ma60 = float(df_history['Close'].tail(60).mean())
    std20 = float(df_history['Close'].tail(20).std())
    bandwidth = (((ma20 + 2*std20) - (ma20 - 2*std20)) / ma20) * 100
    ma_diff = (abs(ma20 - ma60) / ma60) * 100
    amplitude = ((high_p - low_p) / yesterday_close) * 100
    range_hl = high_p - low_p
    power_val = ((close_p - open_p) / range_hl) * today_vol if range_hl > 0 else 0.0
    return {'布林帶寬(%)': round(bandwidth, 2), '均線糾結(%)': round(ma_diff, 2),
            '日內振幅(%)': round(amplitude, 2), '多空力道': round(power_val, 1),
            '月量爆發倍數': round(today_vol / vol20, 1), '五日均量(張)': round(vol5, 0)}


def random_bars(n=80, seed=1):
    rng = np.random.default_rng(seed)
    close = 50 + np.cumsum(rng.normal(0, 1, n))
    open_ = close + rng.normal(0, 0.5, n)
    high = np.maximum(open_, close) + rng.random(n)
    low = np.minimum(open_, close) - rng.random(n)
    vol = rng.integers(100_000, 5_000_000, n)
    idx = pd.bdate_range("2026-06-01", periods=n)
    return pd.DataFrame({'Open': open_, 'High': high, 'Low': low, 'Close': close, 'Volume': vol}, index=idx)


def test_metrics_match_legacy_formula():
    for seed in range(5):
        df = random_bars(seed=seed)
        new = stock_metrics("1234", "測試", df)
        for k, v in legacy_metrics(df).items():
            assert new[k] == v, (seed, k)


def test_compute_metrics_skips_short_and_stale_stocks():
    full = random_bars(80)
    rows = []
    for code, df in {"1111": full, "2222": full.iloc[:40], "3333": full.iloc[:-1]}.items():
        for d, r in df.iterrows():
            rows.append((code, d, r.Open, r.High, r.Low, r.Close, int(r.Volume)))
    bars = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume"])
    metrics, missing = compute_metrics(bars, {"1111": "甲"}, ["1111", "2222", "3333", "4444"])
    assert list(metrics['股票代號']) == ["1111"]
    assert set(missing) == {"2222", "3333", "4444"}
    assert to_ohlcv(bars[bars.code == "1111"]).shape == (80, 5)
