"""觀察名單：把掃描到的股票記下來（日期、價格、進場分數、哪個戰術），之後自動追蹤漲跌，
驗證戰術和進場分數到底準不準。

- 基準價 = 加入當天資料庫最新一日的收盤價。
- 3 天 / 1 週 / 2 週 = 加入日之後第 3、5、10 個交易日的收盤價。
- 同期 0050 用一樣的日期算，才知道是個股強還是大盤強。
"""
from __future__ import annotations

import json
import math
from datetime import datetime

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL, name TEXT, added_date TEXT NOT NULL, added_at TEXT,
    price REAL NOT NULL, entry INTEGER, parts TEXT, stage TEXT, light TEXT,
    source TEXT, note TEXT,
    UNIQUE(code, added_date, source)
);
"""
HORIZONS = (("d3", 3, "3 天"), ("d5", 5, "1 週"), ("d10", 10, "2 週"))
BENCH = "0050"
PATH_DAYS = 10   # 「每日進度」顯示最近幾個交易日
BUCKETS = (("70 分以上", 70, 101), ("50～69 分", 50, 70), ("未滿 50 分", -1, 50))


def _r(v, d=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)


def apply_live(rows: list[dict], quotes: dict[str, dict], today: str, bench: str = BENCH) -> int:
    """盤中：用元大即時報價更新「至今」「期間最高／最低」與同期 0050。回傳更新了幾檔。"""
    b = (quotes.get(bench) or {}).get("price")
    n = 0
    for r in rows:
        q = quotes.get(r["code"]) or {}
        px = q.get("price")
        if not px or r["added_date"] >= today:
            continue
        base = float(r["price"])
        prev_last = r.get("last")
        r.update({"last": px, "last_date": today, "live": True, "ret": _r((px / base - 1) * 100)})
        ref = q.get("prev_close") or prev_last
        r["day_chg"] = _r((px / ref - 1) * 100) if ref else None
        r["path"] = (r.get("path") or [])[-(PATH_DAYS - 1):] + [{"d": today, "ret": r["ret"], "live": True}]
        hi, lo = q.get("high") or px, q.get("low") or px
        up, dn = _r((hi / base - 1) * 100), _r((lo / base - 1) * 100)
        r["max_up"] = up if r.get("max_up") is None else max(r["max_up"], up)
        r["max_dn"] = dn if r.get("max_dn") is None else min(r["max_dn"], dn)
        if b and r.get("bench_base"):
            r["bench_ret"] = _r((b / r["bench_base"] - 1) * 100)
        n += 1
    return n


class Watch:
    def __init__(self, store):
        self.conn = store._conn
        self.conn.executescript(SCHEMA)

    def add(self, item: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO watchlist(code,name,added_date,added_at,price,entry,parts,stage,light,source,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code,added_date,source) DO UPDATE SET "
                "price=excluded.price, entry=excluded.entry, parts=excluded.parts, stage=excluded.stage, "
                "light=excluded.light, note=COALESCE(excluded.note, watchlist.note)",
                (item["code"], item.get("name"), item["added_date"], datetime.now().isoformat(timespec="seconds"),
                 float(item["price"]), item.get("entry"), json.dumps(item.get("parts") or {}),
                 item.get("stage"), item.get("light"), item.get("source") or "手動", item.get("note")))
            row = self.conn.execute("SELECT id FROM watchlist WHERE code=? AND added_date=? AND source=?",
                                    (item["code"], item["added_date"], item.get("source") or "手動")).fetchone()
        return int(row[0]) if row else int(cur.lastrowid)

    def remove(self, wid: int) -> bool:
        with self.conn:
            return self.conn.execute("DELETE FROM watchlist WHERE id=?", (wid,)).rowcount > 0

    def items(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM watchlist ORDER BY added_date DESC, id DESC", self.conn)

    # ------------------------------------------------------------------ 追蹤
    def report(self, bars: pd.DataFrame) -> dict:
        items = self.items()
        if items.empty:
            return {"rows": [], "stats": [], "buckets": [], "horizons": [h[2] for h in HORIZONS]}
        codes = set(items["code"]) | {BENCH}
        b = bars[bars["code"].isin(codes)].copy()
        b["d"] = pd.to_datetime(b["date"]).dt.strftime("%Y-%m-%d")
        by_code = {c: g.sort_values("d").reset_index(drop=True) for c, g in b.groupby("code")}
        bench = by_code.get(BENCH)
        rows = [self._track(r, by_code.get(r["code"]), bench) for r in items.to_dict(orient="records")]
        return {"rows": rows, "stats": _stats(rows, "source"), "buckets": _bucket_stats(rows),
                "horizons": [h[2] for h in HORIZONS]}

    @staticmethod
    def _track(r: dict, g: pd.DataFrame | None, bench: pd.DataFrame | None) -> dict:
        out = {k: r[k] for k in ("id", "code", "name", "added_date", "price", "entry", "stage", "light", "source", "note")}
        out["parts"] = json.loads(r["parts"] or "{}")
        price = float(r["price"])
        if g is None or g.empty:
            return {**out, "days": 0, "last": None, "ret": None}
        idx0 = int((g["d"] <= r["added_date"]).sum()) - 1        # 加入日（或之前最近一個交易日）
        after = g.iloc[idx0 + 1:] if idx0 >= 0 else g
        last = float(g["close"].iloc[-1])
        out.update({"days": len(after), "last": last, "last_date": g["d"].iloc[-1],
                    "ret": _r((last / price - 1) * 100)})
        prev = float(g["close"].iloc[-2]) if len(g) >= 2 else None
        out["day_chg"] = _r((last / prev - 1) * 100) if prev and len(after) else None
        out["path"] = [{"d": d, "ret": _r((float(c) / price - 1) * 100)}
                       for d, c in zip(after["d"].iloc[-PATH_DAYS:], after["close"].iloc[-PATH_DAYS:])]
        if len(after):
            out["max_up"] = _r((float(after["high"].max()) / price - 1) * 100)
            out["max_dn"] = _r((float(after["low"].min()) / price - 1) * 100)
        b0 = None
        if bench is not None and not bench.empty:
            bi = int((bench["d"] <= r["added_date"]).sum()) - 1
            b0 = float(bench["close"].iloc[bi]) if bi >= 0 else None
            bafter = bench.iloc[bi + 1:] if bi >= 0 else bench
        for key, n, _ in HORIZONS:
            if len(after) >= n:
                out[key] = _r((float(after["close"].iloc[n - 1]) / price - 1) * 100)
                if b0 and len(bafter) >= n:
                    out[f"{key}_bench"] = _r((float(bafter["close"].iloc[n - 1]) / b0 - 1) * 100)
        out["bench_base"] = b0
        if b0 and bench is not None:
            out["bench_ret"] = _r((float(bench["close"].iloc[-1]) / b0 - 1) * 100)
        return out


def _summ(rows: list[dict]) -> dict:
    s = {"n": len(rows)}
    for key, _, _ in HORIZONS:
        vals = [r[key] for r in rows if r.get(key) is not None]
        beat = [r[key] > r[f"{key}_bench"] for r in rows if r.get(key) is not None and r.get(f"{key}_bench") is not None]
        s[key] = {"n": len(vals), "win": _r(sum(v > 0 for v in vals) / len(vals) * 100, 0) if vals else None,
                  "avg": _r(sum(vals) / len(vals)) if vals else None,
                  "beat": _r(sum(beat) / len(beat) * 100, 0) if beat else None}
    rets = [r["ret"] for r in rows if r.get("ret") is not None]
    s["ret_avg"] = _r(sum(rets) / len(rets)) if rets else None
    return s


def _stats(rows: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get(key) or "手動", []).append(r)
    out = [{"name": k, **_summ(v)} for k, v in groups.items()]
    out.sort(key=lambda x: -x["n"])
    if len(groups) > 1:
        out.append({"name": "全部", **_summ(rows)})
    return out


def _bucket_stats(rows: list[dict]) -> list[dict]:
    out = []
    for name, lo, hi in BUCKETS:
        sel = [r for r in rows if r.get("entry") is not None and lo <= r["entry"] < hi]
        if sel:
            out.append({"name": name, **_summ(sel)})
    return out
