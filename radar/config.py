"""集中管理設定。預設值寫在這裡，個人設定放在 config.local.toml（不進 Git）。"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = ROOT / "config.local.toml"


@dataclass
class Settings:
    db_path: Path = ROOT / "data" / "radar.db"
    # 建庫時往回抓多少個「交易日」；60 日均線至少需要 60 根，多留緩衝。
    history_trading_days: int = 90
    # 對證交所 / 櫃買中心每次請求之間的間隔秒數，避免被封鎖。
    request_interval_sec: float = 3.0
    # 法人買賣超、融資融券保留幾個交易日（算連買天數、5 日 / 20 日累計）
    chip_trading_days: int = 20
    extra: dict = field(default_factory=dict)
    # config.local.toml 的 [yuanta] 區段（帳號、密碼只存在使用者自己的電腦）
    yuanta: dict = field(default_factory=dict)


def load_settings(path: Path | None = None) -> Settings:
    s = Settings()
    path = path or Path(os.environ.get("RADAR_CONFIG", LOCAL_CONFIG))
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for key, value in data.get("radar", {}).items():
            if hasattr(s, key):
                current = getattr(s, key)
                setattr(s, key, Path(value) if isinstance(current, Path) else value)
            else:
                s.extra[key] = value
        s.yuanta = dict(data.get("yuanta", {}))
    return s
