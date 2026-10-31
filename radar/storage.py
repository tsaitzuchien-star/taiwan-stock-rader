"""SQLite 本地資料庫：股票清單、日 K、抓取紀錄。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,            -- 'TWSE' 上市 / 'TPEX' 上櫃
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL,
    date TEXT NOT NULL,              -- YYYY-MM-DD
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,                  -- 成交股數（股，不是張）
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(date);
CREATE TABLE IF NOT EXISTS fetch_log (
    market TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL,            -- 'ok' 有資料 / 'holiday' 休市 / 'error' 失敗
    rows INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (market, date)
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    # ---------- 寫入 ----------
    def upsert_stocks(self, rows: Iterable[tuple[str, str, str]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.tx() as c:
            c.executemany(
                "INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, "
                "updated_at=excluded.updated_at",
                [(code, name, market, now) for code, name, market in rows],
            )

    def upsert_bars(self, rows: Iterable[tuple]) -> int:
        rows = list(rows)
        with self.tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO daily_bars(code,date,open,high,low,close,volume) "
                "VALUES(?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def log_fetch(self, market: str, day: date, status: str, rows: int = 0, message: str = "") -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO fetch_log(market,date,status,rows,message,fetched_at) "
                "VALUES(?,?,?,?,?,?)",
                (market, day.isoformat(), status, rows, message[:500],
                 datetime.now().isoformat(timespec="seconds")),
            )

    def prune_bars(self, keep_trading_days: int) -> None:
        """只保留最近 N 個交易日，避免資料庫無限長大。"""
        with self.tx() as c:
            row = c.execute(
                "SELECT date FROM (SELECT DISTINCT date FROM daily_bars ORDER BY date DESC LIMIT ?) "
                "ORDER BY date ASC LIMIT 1",
                (keep_trading_days,),
            ).fetchone()
            if row:
                c.execute("DELETE FROM daily_bars WHERE date < ?", (row[0],))

    # ---------- 讀取 ----------
    def fetch_status(self, market: str, day: date) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM fetch_log WHERE market=? AND date=?", (market, day.isoformat())
        ).fetchone()
        return row[0] if row else None

    def stocks(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT code, name, market FROM stocks ORDER BY code", self._conn)

    def bars(self, codes: list[str] | None = None, since: str | None = None) -> pd.DataFrame:
        sql = "SELECT code, date, open, high, low, close, volume FROM daily_bars"
        cond, params = [], []
        if codes:
            cond.append(f"code IN ({','.join('?' * len(codes))})")
            params += list(codes)
        if since:
            cond.append("date >= ?")
            params.append(since)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY code, date"
        df = pd.read_sql_query(sql, self._conn, params=params)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def trading_days(self) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT DISTINCT date FROM daily_bars ORDER BY date")]

    def health(self) -> dict:
        """給介面顯示的資料健康摘要。"""
        days = self.trading_days()
        latest = days[-1] if days else None
        n_stocks = self._conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        n_latest = self._conn.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE date=?", (latest,)).fetchone()[0] if latest else 0
        errors = pd.read_sql_query(
            "SELECT market, date, message, fetched_at FROM fetch_log WHERE status='error' "
            "ORDER BY date DESC LIMIT 20", self._conn)
        return {
            "latest_date": latest,
            "trading_days": len(days),
            "stocks": n_stocks,
            "stocks_on_latest": n_latest,
            "errors": errors,
        }
