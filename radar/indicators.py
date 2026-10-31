"""雷達指標計算。邏輯逐條沿用 V10.9.7 的 execute_radar_scan，只是改成讀資料庫。"""
from __future__ import annotations

import pandas as pd

MIN_BARS = 60

COLUMNS = [
    '股票代號', '股票名稱', '現價(元)', '今日漲跌(%)', '日內振幅(%)', '五日均量(張)', '月均量(20日)',
    '今日成交(張)', '月量爆發倍數', '多空力道', '成長K線', '跳空缺口(%)', '布林帶寬(%)',
    '均線糾結(%)', '季線位置', '強力洗盘訊號', '_is_fake', '_is_washout',
]


def stock_metrics(code: str, name: str, df: pd.DataFrame) -> dict | None:
    """df：單一股票的日 K，欄位 Open/High/Low/Close/Volume（股），依日期由舊到新。"""
    if df is None or len(df) < MIN_BARS:
        return None
    latest, yesterday = df.iloc[-1], df.iloc[-2]
    open_p, close_p = float(latest['Open']), float(latest['Close'])
    high_p, low_p = float(latest['High']), float(latest['Low'])
    today_vol = float(latest['Volume']) / 1000
    yesterday_close = float(yesterday['Close'])
    if yesterday_close == 0:
        return None

    vol5 = float(df['Volume'].tail(5).mean() / 1000)
    vol20 = float(df['Volume'].tail(20).mean() / 1000)
    ma20 = float(df['Close'].tail(20).mean())
    ma60 = float(df['Close'].tail(60).mean())
    std20 = float(df['Close'].tail(20).std())

    bandwidth = (((ma20 + 2 * std20) - (ma20 - 2 * std20)) / ma20) * 100 if ma20 > 0 else 999
    ma_diff = (abs(ma20 - ma60) / ma60) * 100 if ma60 > 0 else 999
    gap_up_pct = ((open_p - yesterday_close) / yesterday_close) * 100
    is_fake = (high_p - max(open_p, close_p)) > abs(close_p - open_p)
    amplitude = ((high_p - low_p) / yesterday_close) * 100

    is_panic_washed = bool(
        amplitude >= 12.0 and today_vol >= vol5 * 2.5
        and ma60 * 0.95 <= close_p <= ma60 * 1.06
    )
    range_hl = high_p - low_p
    power_val = ((close_p - open_p) / range_hl) * today_vol if range_hl > 0 else 0.0
    body_pct = ((close_p - open_p) / yesterday_close) * 100
    upper_shadow = high_p - max(close_p, open_p)
    is_growth_candle = body_pct >= 3.0 and today_vol > vol5 and upper_shadow <= range_hl * 0.25

    return {
        '股票代號': code,
        '股票名稱': name,
        '現價(元)': close_p,
        '今日漲跌(%)': round(((close_p - yesterday_close) / yesterday_close) * 100, 2),
        '日內振幅(%)': round(amplitude, 2),
        '五日均量(張)': round(vol5, 0),
        '月均量(20日)': round(vol20, 0),
        '今日成交(張)': round(today_vol, 0),
        '月量爆發倍數': round(today_vol / vol20, 1) if vol20 > 0 else 0,
        '多空力道': round(power_val, 1),
        '成長K線': '🌟 確立' if is_growth_candle else '-',
        '跳空缺口(%)': round(gap_up_pct, 2),
        '布林帶寬(%)': round(bandwidth, 2),
        '均線糾結(%)': round(ma_diff, 2),
        '季線位置': round(ma60, 2),
        '強力洗盘訊號': '🚨 觸發' if is_panic_washed else '-',
        '_is_fake': bool(is_fake),
        '_is_washout': is_panic_washed,
    }


def to_ohlcv(bars: pd.DataFrame) -> pd.DataFrame:
    """資料庫格式 -> 原程式慣用的 Open/High/Low/Close/Volume + 日期索引。"""
    out = bars.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                               'close': 'Close', 'volume': 'Volume'})
    out = out.set_index('date')[['Open', 'High', 'Low', 'Close', 'Volume']]
    out.index.name = 'Date'
    return out.dropna(subset=['Close'])


def compute_metrics(bars: pd.DataFrame, names: dict[str, str],
                    codes: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """回傳 (指標表, 缺資料的股票代號)。

    最新一天沒有成交的股票（停牌、無量）會列入缺資料，避免把舊資料當成「今日」。
    """
    if bars.empty:
        return pd.DataFrame(columns=COLUMNS), list(codes or [])
    latest_day = bars['date'].max()
    wanted = set(codes) if codes else None
    rows, missing = [], []
    seen = set()
    for code, g in bars.groupby('code', sort=True):
        if wanted is not None and code not in wanted:
            continue
        seen.add(code)
        if g['date'].iloc[-1] != latest_day:
            missing.append(code)
            continue
        m = stock_metrics(code, names.get(code, '未知'), to_ohlcv(g))
        if m is None:
            missing.append(code)
        else:
            rows.append(m)
    if wanted is not None:
        missing += sorted(wanted - seen)
    return pd.DataFrame(rows, columns=COLUMNS), missing
