"""進場分數（0～100）：判斷雷達掃描出來的股票是「剛起漲」還是「已經漲一波」。

四個部分加總：
- 位置 30：離季線越近、近 20 日漲越少越好（剛離開盤整區）；漲一波的扣到 0。
- 突破品質 25：量放大、收在當日高點附近、今天真的有漲；只有換手沒有價格的假量給 0。
- 盤整基礎 15：發動前（5 天前那 20 天）布林帶寬越窄，代表盤得越久、越紮實。
- 法人籌碼 30：法人選股的綜合評分 × 0.3。

70 分以上 🟢、50～69 🟡、未滿 50 🔴。只是規則化的參考，不是投資建議。
"""
from __future__ import annotations

import math

import pandas as pd

PARTS = (("pos", "位置", 30), ("brk", "突破", 25), ("base", "盤整", 15), ("chip", "法人", 30))
GREEN, YELLOW = 70, 50


def _num(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def stage(bias: float, ret20: float) -> str:
    """位階：季線下 / 起漲 / 中段 / 高檔。"""
    if bias >= 20 or ret20 >= 30:
        return "高檔"
    if bias < -3:
        return "季線下"
    if bias > 10 or ret20 >= 15:
        return "中段"
    return "起漲"


def _position(bias: float, ret20: float) -> tuple[float, str]:
    if bias < -3:
        s = 0.0
    elif bias < 0:
        s = 15.0
    elif bias <= 8:
        s = 30.0
    elif bias <= 15:
        s = 30 - (bias - 8) * 1.5
    else:
        s = max(0.0, 19.5 - (bias - 15) * 1.95)
    if ret20 > 15:
        s = max(0.0, s - (ret20 - 15))
    if bias < 0:
        why = f"還在季線下方 {abs(bias):.1f}%，趨勢未翻多"
    elif s >= 25:
        why = f"離季線 {bias:.1f}%、近 20 日漲 {ret20:.1f}%，剛起漲"
    elif s >= 12:
        why = f"離季線 {bias:.1f}%、近 20 日漲 {ret20:.1f}%，已漲一段"
    else:
        why = f"離季線 {bias:.1f}%、近 20 日漲 {ret20:.1f}%，已漲一波，追高風險大"
    return s, why


def _breakout(vr: float, close_pos: float, chg: float, amp: float, power: float) -> tuple[float, str]:
    if amp < 1.5 and vr >= 3:
        return 0.0, f"量是平常 {vr:.1f} 倍但振幅只有 {amp:.1f}%，像大戶換手不是突破"
    s = min(max(vr, 0), 4) / 4 * 10 + max(0.0, min(close_pos, 1.0)) * 10
    s += 5 if chg >= 2 else 2.5 if chg > 0 else 0
    if power < 0:
        s = min(s, 8.0)
        why = f"量 {vr:.1f} 倍但收在低檔，賣壓重"
    elif close_pos >= 0.8 and chg > 0:
        why = f"量 {vr:.1f} 倍、收在當日高點附近，買盤強"
    else:
        why = f"量 {vr:.1f} 倍、收盤在當日區間 {close_pos * 100:.0f}% 位置"
    return s, why


def _base(pre_bw: float) -> tuple[float, str]:
    if pre_bw <= 8:
        s = 15.0
    elif pre_bw <= 15:
        s = 15 - (pre_bw - 8)
    else:
        s = max(0.0, 8 - (pre_bw - 15) * 8 / 15)
    if s >= 12:
        why = f"發動前布林寬 {pre_bw:.1f}%，盤整紮實"
    elif s >= 6:
        why = f"發動前布林寬 {pre_bw:.1f}%，盤整普通"
    else:
        why = f"發動前布林寬 {pre_bw:.1f}%，之前就大漲大跌過"
    return s, why


def _chip(alpha_row: dict | None) -> tuple[float, str, float | None]:
    if not alpha_row:
        return 15.0, "沒有法人資料，先給中間分", None
    score = _num(alpha_row.get("score"), 50.0)
    f5, t5 = _num(alpha_row.get("f5"), math.nan), _num(alpha_row.get("t5"), math.nan)
    if math.isnan(f5):
        flow = "法人資料不足"
    else:
        flow = f"外資 5 日 {f5:+,.0f} 張、投信 {t5:+,.0f} 張"
    return score * 0.3, f"法人評分 {score:.0f}（{flow}）", score


def score_row(m: dict, alpha_row: dict | None) -> dict:
    """m：indicators.stock_metrics 的英文欄位版（見 server.COLMAP）。"""
    bias, ret20 = _num(m.get("bias")), _num(m.get("ret20"))
    parts = {}
    reasons = []
    for key, (s, why) in {
        "pos": _position(bias, ret20),
        "brk": _breakout(_num(m.get("vr")), _num(m.get("close_pos"), 0.5), _num(m.get("chg")),
                         _num(m.get("amp")), _num(m.get("power"))),
        "base": _base(_num(m.get("pre_bw"), 999)),
    }.items():
        parts[key] = round(s, 1)
        reasons.append(why)
    chip, why, alpha = _chip(alpha_row)
    parts["chip"] = round(chip, 1)
    reasons.append(why)
    total = round(sum(parts.values()))
    light = "green" if total >= GREEN else "yellow" if total >= YELLOW else "red"
    return {"entry": total, "entry_parts": parts, "light": light, "stage": stage(bias, ret20),
            "alpha": None if alpha is None else round(alpha), "reasons": reasons}


def alpha_lookup(alpha_df: pd.DataFrame) -> dict[str, dict]:
    if alpha_df is None or alpha_df.empty:
        return {}
    cols = [c for c in ("code", "score", "f5", "t5") if c in alpha_df]
    return {r["code"]: r for r in alpha_df[cols].to_dict(orient="records")}
