"""五大戰術預設值與篩選條件（沿用 V10.9.7 apply_mask_and_style 的邏輯）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class ScanParams:
    p: float = 120.0      # 股價上限（元）
    v: float = 3000       # 月均量上限（張）
    tv: float = 500       # 今日成交下限（張）
    pm: float = 1.2       # 月量爆發倍數下限
    sqz: bool = False     # 布林帶寬 <= 15%
    ma: bool = False      # 月季線糾結 <= 3%
    gap: bool = False     # 跳空開高 >= 2%
    wash: bool = True     # 極端洗盤換手
    lu: bool = False      # 近 3 日有漲停，今天收在高檔
    nh: bool = False      # 收盤創 60 日新高
    base: bool = False    # 發動前盤整（前期布林寬 ≤ 20%）且 20 日漲幅 ≤ 40%

    def to_dict(self) -> dict:
        return asdict(self)


TACTICS = [
    {"id": "dive", "name": "深海潛艦", "desc": "經典起漲，季線護盤",
     "params": ScanParams(120.0, 3000, 500, 1.2, False, False, False, True)},
    {"id": "wrath", "name": "大怒神", "desc": "極端洗盤換手",
     "params": ScanParams(80.0, 2000, 1500, 2.5, False, False, False, True)},
    {"id": "blitz", "name": "閃電戰", "desc": "布林壓縮後跳空突破",
     "params": ScanParams(50.0, 1000, 500, 2.5, True, False, True, False)},
    {"id": "ox", "name": "老牛翻身", "desc": "均線糾結，穩健推升",
     "params": ScanParams(100.0, 3000, 1000, 2.0, False, True, False, False)},
    {"id": "monster", "name": "妖股獵人", "desc": "久盤後漲停創新高，主力點火",
     "params": ScanParams(80.0, 3000, 500, 3.0, False, False, False, False, True, True, True)},
]
TACTIC_BY_ID = {t["id"]: t for t in TACTICS}


def apply_filters(df: pd.DataFrame, cfg: ScanParams) -> pd.DataFrame:
    """回傳符合條件的股票（已去掉內部欄位），依月量爆發倍數由大到小排序。"""
    if df.empty:
        return df.drop(columns=["_is_fake", "_is_washout", "_new_high60"], errors="ignore")
    mask = ((df['現價(元)'] <= cfg.p) & (df['今日成交(張)'] >= cfg.tv)
            & (df['月均量(20日)'] <= cfg.v) & (df['月量爆發倍數'] >= cfg.pm)
            & (~df['_is_fake'].astype(bool))
            & (df['現價(元)'] >= df['季線位置'] * 0.95))
    if cfg.sqz:
        mask &= df['布林帶寬(%)'] <= 15
    if cfg.ma:
        mask &= df['均線糾結(%)'] <= 3
    if cfg.gap:
        mask &= df['跳空缺口(%)'] >= 2.0
    if cfg.wash:
        mask &= df['_is_washout'].astype(bool)
    if cfg.lu:
        mask &= (df['近3日漲停(次)'] >= 1) & (df['_close_pos'] >= 0.7) & (df['今日漲跌(%)'] > 0)
    if cfg.nh:
        mask &= df['_new_high60'].astype(bool)
    if cfg.base:
        mask &= (df['前期布林寬(%)'] <= 20) & (df['20日漲幅(%)'] <= 40)
    out = df[mask].drop(columns=['_is_fake', '_is_washout', '_new_high60'], errors='ignore').drop_duplicates(subset=['股票代號'])
    return out.sort_values('月量爆發倍數', ascending=False).reset_index(drop=True)
