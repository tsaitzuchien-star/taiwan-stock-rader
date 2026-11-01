"""持倉防禦約談：真實稅費損益、跌停壓力測試、季線防線與裁決（沿用 V10.9.7 第三艙規則）。"""
from __future__ import annotations

import pandas as pd

FEE_RATE = 0.001425
TAX_RATE = 0.003


def _fee(amount: float, discount: float) -> int:
    return max(1, int(amount * FEE_RATE * discount))


def evaluate(cost: float, shares: int, closes: pd.Series, fee_discount: float = 1.0,
             heat: int | None = None, blind_spot: bool = False) -> dict:
    """closes：依日期由舊到新的收盤價。回傳畫面要顯示的數字與裁決。"""
    closes = closes.dropna().astype(float)
    if closes.empty or cost <= 0 or shares <= 0:
        raise ValueError("需要收盤價、成本與股數")
    price = float(closes.iloc[-1])
    ma = lambda n: float(closes.tail(n).mean())  # noqa: E731
    ma10, ma20, ma60 = ma(10), ma(20), ma(60)

    total_cost = cost * shares + _fee(cost * shares, fee_discount)
    net_now = price * shares - _fee(price * shares, fee_discount) - int(price * shares * TAX_RATE)
    pl = net_now - total_cost
    roi = pl / total_cost * 100

    limit_down = price * 0.9
    net_ld = limit_down * shares - _fee(limit_down * shares, fee_discount) - int(limit_down * shares * TAX_RATE)
    pl_ld = net_ld - total_cost
    bias = (price - ma60) / ma60 * 100 if ma60 > 0 else 0.0

    level, title, text = _verdict(roi, price, cost, ma20, ma60, limit_down, bias, heat, blind_spot)
    out = {
        "price": price, "ma10": ma10, "ma20": ma20, "ma60": ma60, "bias_ma60_pct": bias,
        "total_cost": total_cost, "net_value": net_now, "unrealized": pl, "roi_pct": roi,
        "limit_down_price": limit_down, "limit_down_pl": pl_ld,
        "escape_weak": ma10 if price < ma60 else None,
        "escape_strong": ma20 if price < ma60 else None,
        "verdict": {"level": level, "title": title, "text": text},
    }
    return out


def _verdict(roi, price, cost, ma20, ma60, limit_down, bias, heat, blind_spot):
    below = price < ma60
    if roi < -15:
        if below or blind_spot:
            return ("danger", "深度套牢且破線，啟動逃命波戰術",
                    "部位已嚴重虧損且跌破季線，繼續死抱將面臨無底洞風險。不要在早盤恐慌殺低，"
                    "等反彈靠近十日線或月線時賣出，保留現金。")
        return ("warn", "深度套牢，但季線有守，被動防禦",
                f"帳面虧損 {roi:.2f}%，但股價尚未跌破季線（{ma60:.2f}）。現在殺出容易砍在低點，"
                "以季線為防守線，收盤跌破再停損；守住則等待反彈。")
    if roi < -5:
        return ("warn", "中度虧損，嚴格觀察",
                f"帳面虧損 {roi:.2f}%，正處於停損邊緣。若股價在季線之下，逢反彈減碼；在季線之上，緊盯季線防守。")
    if roi < 0:
        if below:
            return ("warn", "微幅虧損，但已破線",
                    "虧損不大但已跌破季線，趨勢轉弱。建議趁虧損還小停損或換股。")
        return ("ok", "正常震盪，防線內持續持有",
                "跌幅在 5% 以內，屬正常震盪，且股價站穩季線之上，不要被洗出場。")
    if (heat is not None and heat >= 9) or bias >= 20.0 or blind_spot:
        reasons = []
        if heat is not None and heat >= 9:
            reasons.append(f"PTT 討論 {heat} 篇，散戶大量湧入")
        if bias >= 20.0:
            reasons.append(f"季線乖離 {bias:.1f}%，技術面超買")
        if blind_spot:
            reasons.append("論壇出現獲利了結或出貨疑慮")
        return ("take", "功成身退，建議獲利了結",
                "已達過熱狀態：" + "；".join(reasons) + "。不要貪最後一段魚尾，分批獲利了結。")
    if limit_down < cost or limit_down < ma20:
        return ("warn", "獲利微薄，嚴格保本",
                f"帳面有賺，但若開盤跌停會跌破成本（{cost:g}）或月線。設好觸價單，跌破成本就保本退場。")
    return ("ok", "防禦深厚，持續持有", "成本夠低，就算跌停也遠高於成本與季線，可以抱緊。")
