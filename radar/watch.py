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


# ------------------------------------------------------------------ 每日檢討
def _avg(vals):
    vals = [v for v in vals if v is not None]
    return _r(sum(vals) / len(vals)) if vals else None


def daily_review(rows: list[dict], market: dict, extra: dict[str, dict] | None = None) -> dict | None:
    """觀察名單當天的檢討：今天賺賠的主因、哪一類股票特別傷、之後怎麼修正。全部是規則化的判讀，不是投資建議。

    market: {"date", "bench_chg", "up", "down", "above_ma60_pct"}；extra: {code: {"industry", "ret20"}}。
    """
    extra = extra or {}
    sel = [r for r in rows if r.get("day_chg") is not None]
    if not sel:
        return None
    n = len(sel)
    avg = _avg([r["day_chg"] for r in sel])
    bench = market.get("bench_chg")
    up, down = market.get("up") or 0, market.get("down") or 0
    down_ratio = down / (up + down) if up + down else None
    losers = [r for r in sel if r["day_chg"] < 0]
    beat = [r for r in sel if bench is not None and r["day_chg"] > bench]

    def item(r):
        e = extra.get(r["code"], {})
        return {"code": r["code"], "name": r.get("name"), "day_chg": r["day_chg"], "ret": r.get("ret"),
                "entry": r.get("entry"), "source": r.get("source"), "industry": e.get("industry")}

    def groups(keyf, min_n=1):
        g: dict[str, list[float]] = {}
        for r in sel:
            k = keyf(r)
            if k:
                g.setdefault(k, []).append(r["day_chg"])
        out = [{"name": k, "n": len(v), "avg": _avg(v)} for k, v in g.items() if len(v) >= min_n]
        return sorted(out, key=lambda x: x["avg"])

    def bucket(r):
        e = r.get("entry")
        return None if e is None else next((name for name, lo, hi in BUCKETS if lo <= e < hi), None)

    by_bucket = groups(bucket)
    by_source = groups(lambda r: r.get("source"))
    by_ind = groups(lambda r: extra.get(r["code"], {}).get("industry"), min_n=2)

    # 漲多股：今天以前 20 日漲幅 ≥ 20%
    def ret20_before(r):
        v = extra.get(r["code"], {}).get("ret20")
        return None if v is None else ((1 + v / 100) / (1 + r["day_chg"] / 100) - 1) * 100
    hot = [r for r in sel if (ret20_before(r) or 0) >= 20]
    cold = [r for r in sel if r not in hot]

    causes, fixes = [], []
    big_drop = bench is not None and bench <= -1.5
    crash = (down_ratio or 0) >= 0.75 or (big_drop and (down_ratio or 0) >= 0.6)
    if crash:
        causes.append({"level": "danger", "title": "大盤系統性重挫是主因",
                       "text": f"0050 {bench:+.1f}%，全市場下跌 {down} 家、上漲 {up} 家；"
                               f"觀察名單 {n} 檔中 {len(losers)} 檔下跌。這種日子幾乎沒有股票躲得掉，不是個別選股失準。"})
        fixes.append("加「大盤濾網」：0050 單日跌超過 1.5%、或下跌家數超過 75%、或站上季線的股票不到 30% 時，暫停新進場、新加入的部位減半。")
    elif big_drop:
        causes.append({"level": "warn", "title": "權值股重挫，但多數股票沒跟跌",
                       "text": f"0050 {bench:+.1f}%，可是全市場上漲 {up} 家、下跌 {down} 家，賣壓集中在大型權值股。"})
    elif bench is not None and bench < 0:
        causes.append({"level": "warn", "title": "大盤偏弱",
                       "text": f"0050 {bench:+.1f}%，下跌 {down} 家、上漲 {up} 家。"})
    if avg is not None and bench is not None:
        diff = avg - bench
        if diff <= -1:
            causes.append({"level": "warn", "title": f"觀察股比大盤多跌 {abs(diff):.1f}%",
                           "text": f"平均 {avg:+.1f}%，只有 {len(beat)}/{n} 檔贏 0050。選到的多是波動大的中小型股，大盤一跌就跌更兇（高 β）。"})
            fixes.append("名單裡高波動股（妖股、中小型）要搭配更緊的停損，或同時放幾檔權值股平衡。")
        elif diff >= 1:
            causes.append({"level": "ok", "title": f"觀察股比大盤抗跌 {diff:.1f}%",
                           "text": f"平均 {avg:+.1f}%，{len(beat)}/{n} 檔贏 0050，選股本身有加分。"})
    hi = next((b for b in by_bucket if b["name"] == BUCKETS[0][0]), None)
    lo = next((b for b in by_bucket if b["name"] == BUCKETS[2][0]), None)
    if hi and lo and hi["n"] >= 2 and lo["n"] >= 2:
        gap = hi["avg"] - lo["avg"]
        if gap >= 1:
            causes.append({"level": "ok", "title": "進場分數有效",
                           "text": f"70 分以上平均 {hi['avg']:+.1f}%（{hi['n']} 檔），未滿 50 分平均 {lo['avg']:+.1f}%（{lo['n']} 檔），高分股少跌 {gap:.1f}%。"})
            fixes.append("只對 70 分以上（綠燈）的股票進場；未滿 50 分的只觀察、不買。")
        elif gap <= -1:
            causes.append({"level": "warn", "title": "今天進場分數沒幫上忙",
                           "text": f"70 分以上平均 {hi['avg']:+.1f}%，反而比未滿 50 分的 {lo['avg']:+.1f}% 差，要多看幾天再判斷。"})
    if len(hot) >= 2 and cold:
        ha, ca = _avg([r["day_chg"] for r in hot]), _avg([r["day_chg"] for r in cold])
        if ha is not None and ca is not None and ha <= ca - 1:
            causes.append({"level": "warn", "title": "漲多的跌最兇",
                           "text": f"之前 20 日已漲 20% 以上的 {len(hot)} 檔平均 {ha:+.1f}%，其他股票平均 {ca:+.1f}%。獲利了結賣壓集中在漲多股。"})
            fixes.append("20 日已漲超過 20% 的不追高，等回測月線（20 日線）站穩再進。")
    if by_ind and by_ind[0]["avg"] is not None and avg is not None and by_ind[0]["avg"] <= avg - 1.5:
        w = by_ind[0]
        causes.append({"level": "warn", "title": f"{w['name']}族群拖累",
                       "text": f"{w['n']} 檔平均 {w['avg']:+.1f}%，比整份名單平均差 {avg - w['avg']:.1f}%。同族群放太多檔，風險會集中。"})
        fixes.append("同一個產業最多放 2～3 檔，分散族群風險。")
    stop = [r for r in rows if r.get("ret") is not None and r["ret"] <= -8]
    if stop:
        names = "、".join(f"{r['code']} {r.get('name') or ''}".strip() for r in stop[:6])
        causes.append({"level": "danger", "title": f"{len(stop)} 檔已跌破 -8% 停損線", "text": names})
        fixes.append("加入後跌超過 8% 的照紀律出場（擂台也是這條規則），不要凹單。")
    if not fixes:
        fixes.append("今天沒有明顯的系統性問題，照原本規則繼續追蹤。")
    sel_sorted = sorted(sel, key=lambda r: r["day_chg"])
    return {"date": market.get("date"), "n": n, "avg": avg, "bench_chg": _r(bench), "beat": len(beat),
            "losers": len(losers), "up": up, "down": down, "above_ma60_pct": _r(market.get("above_ma60_pct"), 1),
            "worst": [item(r) for r in sel_sorted[:3]], "best": [item(r) for r in sel_sorted[::-1][:3]],
            "by_bucket": by_bucket, "by_source": by_source, "by_industry": by_ind[:5],
            "causes": causes, "fixes": fixes}


def review_md(rv: dict) -> str:
    """每日檢討存成 Markdown（週六檢討會議用）。"""
    f = lambda v: "—" if v is None else f"{v:+.2f}%"  # noqa: E731
    lines = [f"# 觀察名單每日檢討 {rv.get('date')}", "",
             f"- 觀察 {rv['n']} 檔：平均 {f(rv['avg'])}，0050 {f(rv['bench_chg'])}，贏 0050 {rv['beat']} 檔，下跌 {rv['losers']} 檔",
             f"- 全市場：上漲 {rv['up']} 家、下跌 {rv['down']} 家，站上季線 {rv.get('above_ma60_pct')}%", "", "## 主因"]
    lines += [f"- **{c['title']}**：{c['text']}" for c in rv["causes"]] or ["- （無）"]
    lines += ["", "## 修正方向"] + [f"- {x}" for x in rv["fixes"]]
    lines += ["", "## 跌最多", *[f"- {w['code']} {w.get('name') or ''} {f(w['day_chg'])}（分數 {w.get('entry')}，{w.get('source')}）" for w in rv["worst"]]]
    lines += ["", "## 最抗跌", *[f"- {w['code']} {w.get('name') or ''} {f(w['day_chg'])}（分數 {w.get('entry')}，{w.get('source')}）" for w in rv["best"]]]
    lines += ["", "## 進場分數分組", *[f"- {b['name']}：{b['n']} 檔，平均 {f(b['avg'])}" for b in rv["by_bucket"]]]
    lines += ["", "## 戰術分組", *[f"- {b['name']}：{b['n']} 檔，平均 {f(b['avg'])}" for b in rv["by_source"]]]
    lines += ["", "_規則化判讀，不是投資建議。_", ""]
    return "\n".join(lines)
