"""百萬擂台的每日交易日誌與每週檢討報告。

所有數字都從資料庫裡的擂台紀錄算出來（arena_nav、arena_trades、arena_orders），
畫面、下載的週報、自動存檔的 Markdown／CSV 用的是同一份資料。
"""
from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import arena as A

EXIT_KINDS = ("停損", "停利", "跌破季線", "評分轉弱", "汰弱換股")
WEEKDAY = "一二三四五六日"


def _f(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        return v
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def exit_kind(reason: str | None) -> str:
    for k in EXIT_KINDS:
        if reason and reason.startswith(k):
            return k
    return "其他"


def monday(d: str) -> str:
    x = date.fromisoformat(d)
    return (x - timedelta(days=x.weekday())).isoformat()


class Journal:
    def __init__(self, store):
        self.store = store
        conn = store._conn
        conn.executescript(A.SCHEMA)
        self.state = A.Arena(store)._get()
        self.nav = pd.read_sql_query("SELECT * FROM arena_nav ORDER BY date", conn)
        self.trades = pd.read_sql_query("SELECT * FROM arena_trades ORDER BY id", conn)
        self.orders = pd.read_sql_query("SELECT * FROM arena_orders ORDER BY id", conn)
        self.days = list(self.nav["date"])
        codes = sorted(set(self.trades["code"])) if not self.trades.empty else []
        closes = store.bars(codes=codes, since=self.days[0]) if codes and self.days else pd.DataFrame()
        self.close = {}
        if not closes.empty:
            closes["d"] = closes["date"].dt.strftime("%Y-%m-%d")
            self.close = {(r.code, r.d): float(r.close) for r in closes.itertuples() if pd.notna(r.close)}

    @property
    def ready(self) -> bool:
        return self.state is not None and bool(self.days)

    def phase(self, d: str) -> str:
        row = self.nav[self.nav["date"] == d]
        return "實戰" if not row.empty and row.iloc[0]["phase"] == "live" else "回測"

    # ------------------------------------------------------------ 持股還原
    def holdings(self, d: str) -> list[dict]:
        """用成交紀錄還原 d 日收盤時的持股，市值用 d 日收盤價。"""
        pos: dict[str, dict] = {}
        for t in self.trades[self.trades["date"] <= d].itertuples():
            if t.side == "buy":
                pos[t.code] = {"code": t.code, "name": t.name, "shares": int(t.shares), "entry": t.date,
                               "entry_price": float(t.price), "cost": float(t.amount + t.fee), "reason": t.reason}
            else:
                pos.pop(t.code, None)
        nav_row = self.nav[self.nav["date"] == d]
        nav = float(nav_row.iloc[0]["nav"]) if not nav_row.empty else None
        out = []
        for p in pos.values():
            last = self._last_close(p["code"], d) or p["entry_price"]
            value = last * p["shares"]
            held = sum(1 for x in self.days if p["entry"] <= x <= d)
            out.append({**p, "avg": p["cost"] / p["shares"], "close": last, "value": value,
                        "pnl": value - p["cost"], "pnl_pct": (value / p["cost"] - 1) * 100,
                        "days": held, "weight": value / nav * 100 if nav else None})
        return sorted(out, key=lambda x: -x["value"])

    def _last_close(self, code: str, d: str) -> float | None:
        for x in reversed([x for x in self.days if x <= d]):
            if (code, x) in self.close:
                return self.close[(code, x)]
        return None

    # ------------------------------------------------------------ 每日
    def day(self, d: str | None = None) -> dict:
        if not self.ready:
            return {"ready": False}
        d = d if d in self.days else self.days[-1]
        i = self.days.index(d)
        row = self.nav.iloc[i]
        prev = self.nav.iloc[i - 1] if i > 0 else None
        base_nav = float(prev["nav"]) if prev is not None else A.START_CASH
        base_bench = float(prev["bench"]) if prev is not None and pd.notna(prev["bench"]) else A.START_CASH
        tr = self.trades[self.trades["date"] == d]
        missed = self.orders[(self.orders["exec_date"] == d) & (self.orders["status"] != "成交")]
        decided = self._decisions(d, i)
        holds = self.holdings(d)
        sells = tr[tr["side"] == "sell"]
        return {
            "ready": True, "date": d, "weekday": WEEKDAY[date.fromisoformat(d).weekday()], "phase": self.phase(d),
            "prev": self.days[i - 1] if i > 0 else None, "next": self.days[i + 1] if i + 1 < len(self.days) else None,
            "nav": float(row["nav"]), "cash": float(row["cash"]), "value": float(row["value"]),
            "day_ret": (float(row["nav"]) / base_nav - 1) * 100,
            "bench_ret": (float(row["bench"]) / base_bench - 1) * 100 if pd.notna(row["bench"]) else None,
            "total_ret": (float(row["nav"]) / A.START_CASH - 1) * 100,
            "buys": _records(tr[tr["side"] == "buy"]), "sells": _records(sells),
            "realized": float(sells["pnl"].sum()) if not sells.empty else 0.0,
            "fees": float((tr["fee"] + tr["tax"]).sum()) if not tr.empty else 0.0,
            "missed": _records(missed), "holdings": holds, "decisions": decided,
        }

    def _decisions(self, d: str, i: int) -> list[dict]:
        rows = self.orders[self.orders["decided"] == d]
        if not rows.empty:
            out = _records(rows)
            for o in out:
                o["why"] = json.loads(o.get("why") or "[]")
            return out
        if self.state and d == self.state.get("last"):
            return [{**o, "status": "明天開盤執行"} for o in self.state.get("orders", [])]
        if i + 1 < len(self.days):   # 舊紀錄沒有委託表：用隔天的成交代替
            nxt = self.trades[self.trades["date"] == self.days[i + 1]]
            return [{"side": t.side, "code": t.code, "name": t.name, "reason": t.reason, "status": "成交",
                     "price": float(t.price), "why": []} for t in nxt.itertuples()]
        return []

    # ------------------------------------------------------------ 每週
    def weeks(self) -> list[str]:
        return sorted({monday(d) for d in self.days}, reverse=True)

    def week(self, start: str | None = None) -> dict:
        if not self.ready:
            return {"ready": False}
        weeks = self.weeks()
        start = monday(start) if start else weeks[0]
        end = (date.fromisoformat(start) + timedelta(days=6)).isoformat()
        days = [d for d in self.days if start <= d <= end]
        if not days:
            return {"ready": False, "weeks": weeks}
        before = [d for d in self.days if d < start]
        nv = self.nav.set_index("date")
        base_nav = float(nv.at[before[-1], "nav"]) if before else A.START_CASH
        b0 = nv.at[before[-1], "bench"] if before else A.START_CASH
        last = nv.loc[days[-1]]
        ret = (float(last["nav"]) / base_nav - 1) * 100
        bret = (float(last["bench"]) / float(b0) - 1) * 100 if pd.notna(last["bench"]) and pd.notna(b0) else None
        curve = pd.Series([base_nav] + [float(nv.at[d, "nav"]) for d in days])
        mdd = float((curve / curve.cummax() - 1).min() * 100)
        daily, prev_nav, prev_b = [], base_nav, float(b0) if pd.notna(b0) else None
        for d in days:
            r = nv.loc[d]
            b = float(r["bench"]) if pd.notna(r["bench"]) else None
            daily.append({"date": d, "weekday": WEEKDAY[date.fromisoformat(d).weekday()], "phase": self.phase(d),
                          "nav": float(r["nav"]), "ret": (float(r["nav"]) / prev_nav - 1) * 100,
                          "bench_ret": (b / prev_b - 1) * 100 if b and prev_b else None,
                          "holdings": int(r["holdings"]),
                          "buys": int(((self.trades["date"] == d) & (self.trades["side"] == "buy")).sum()),
                          "sells": int(((self.trades["date"] == d) & (self.trades["side"] == "sell")).sum())})
            prev_nav, prev_b = float(r["nav"]), b
        tr = self.trades[(self.trades["date"] >= start) & (self.trades["date"] <= end)]
        sells = tr[tr["side"] == "sell"].copy()
        wins, losses = sells[sells["pnl"] > 0], sells[sells["pnl"] <= 0]
        kinds = []
        if not sells.empty:
            sells["kind"] = sells["reason"].map(exit_kind)
            for k, g in sells.groupby("kind"):
                kinds.append({"kind": k, "n": len(g), "pnl": float(g["pnl"].sum()),
                              "avg_pct": float(g["pnl_pct"].mean())})
            kinds.sort(key=lambda x: -x["n"])
        missed = self.orders[(self.orders["exec_date"] >= start) & (self.orders["exec_date"] <= end)
                             & (self.orders["status"] != "成交")]
        holds = self.holdings(days[-1])
        best = sells.sort_values("pnl_pct", ascending=False).head(1)
        worst = sells.sort_values("pnl_pct").head(1)
        return {
            "ready": True, "start": start, "end": days[-1], "days": len(days), "weeks": weeks,
            "phase": "實戰" if all(x["phase"] == "實戰" for x in daily) else
                     "回測" if all(x["phase"] == "回測" for x in daily) else "回測＋實戰",
            "nav_start": base_nav, "nav_end": float(last["nav"]), "pnl": float(last["nav"]) - base_nav,
            "ret": ret, "bench_ret": bret, "excess": ret - bret if bret is not None else None, "mdd": mdd,
            "grade": A._grade(ret - bret if bret is not None else None, mdd),
            "total_ret": (float(last["nav"]) / A.START_CASH - 1) * 100,
            "daily": daily, "trades": _records(tr),
            "n_buy": int((tr["side"] == "buy").sum()), "n_sell": len(sells),
            "wins": len(wins), "losses": len(losses),
            "win_rate": len(wins) / len(sells) * 100 if len(sells) else None,
            "avg_win": float(wins["pnl_pct"].mean()) if len(wins) else None,
            "avg_loss": float(losses["pnl_pct"].mean()) if len(losses) else None,
            "realized": float(sells["pnl"].sum()) if len(sells) else 0.0,
            "fees": float((tr["fee"] + tr["tax"]).sum()) if len(tr) else 0.0,
            "best": _records(best)[0] if len(best) else None, "worst": _records(worst)[0] if len(worst) else None,
            "exit_kinds": kinds, "missed": _records(missed), "holdings": holds,
            "unrealized": sum(h["pnl"] for h in holds),
        }


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _f(v) for k, v in r.items()} for r in df.to_dict(orient="records")] if len(df) else []


# ---------------------------------------------------------------- Markdown
def _n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}"


def _s(v, d=2, unit="%"):
    return "—" if v is None else f"{v:+,.{d}f}{unit}"


def _lots(shares: int) -> str:
    if shares >= 1000:
        z, r = divmod(int(shares), 1000)
        return f"{z} 張" + (f" {r} 股" if r else "")
    return f"{int(shares)} 股（零股）"


def day_md(j: dict) -> str:
    if not j.get("ready"):
        return "# 百萬擂台每日日誌\n\n還沒有資料。\n"
    L = [f"# 百萬擂台每日日誌 {j['date']}（週{j['weekday']}・{j['phase']}）", "",
         f"- 總資產 **{_n(j['nav'])}** 元（現金 {_n(j['cash'])}、股票 {_n(j['value'])}）",
         f"- 今日 {_s(j['day_ret'])}，同日 0050 {_s(j['bench_ret'])}；開賽至今 {_s(j['total_ret'])}",
         f"- 今日已實現損益 {_s(j['realized'], 0, ' 元')}，手續費＋證交稅 {_n(j['fees'])} 元", ""]
    L += ["## 進場（今天開盤買進）", ""]
    if j["buys"]:
        L += ["| 股票 | 成交價 | 股數 | 金額 | 手續費 | 理由 |", "|---|---:|---:|---:|---:|---|"]
        L += [f"| {t['code']} {t['name']} | {t['price']:.2f} | {_lots(t['shares'])} | {_n(t['amount'])} | {_n(t['fee'])} | {t['reason']} |"
              for t in j["buys"]]
    else:
        L.append("今天沒有買進。")
    L += ["", "## 出場（今天開盤賣出）", ""]
    if j["sells"]:
        L += ["| 股票 | 成交價 | 股數 | 損益 | 報酬 | 出場原因 |", "|---|---:|---:|---:|---:|---|"]
        L += [f"| {t['code']} {t['name']} | {t['price']:.2f} | {_lots(t['shares'])} | {_s(t['pnl'], 0, '')} | {_s(t['pnl_pct'], 1)} | {t['reason']} |"
              for t in j["sells"]]
    else:
        L.append("今天沒有賣出。")
    if j["missed"]:
        L += ["", "## 沒有成交的委託", ""]
        L += [f"- {'買進' if o['side'] == 'buy' else '賣出'} {o['code']} {o['name']}：{o['status']}"
              + (f"（開盤價 {o['price']:.2f}）" if o.get("price") else "") for o in j["missed"]]
    L += ["", f"## 收盤持股（{len(j['holdings'])} / {A.MAX_POSITIONS} 檔）", ""]
    if j["holdings"]:
        L += ["| 股票 | 買進日 | 成本 | 收盤 | 股數 | 市值 | 損益 | 報酬 | 持有 | 比重 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        L += [f"| {h['code']} {h['name']} | {h['entry']} | {h['avg']:.2f} | {h['close']:.2f} | {_lots(h['shares'])} | "
              f"{_n(h['value'])} | {_s(h['pnl'], 0, '')} | {_s(h['pnl_pct'], 1)} | {h['days']} 日 | {_n(h['weight'], 1)}% |"
              for h in j["holdings"]]
    else:
        L.append("空手。")
    L += ["", "## 收盤後決定（下一個交易日開盤執行）", ""]
    if j["decisions"]:
        for o in j["decisions"]:
            act = "買進" if o["side"] == "buy" else "賣出"
            st = f"→ {o['status']}" if o.get("status") else ""
            L.append(f"- **{act} {o['code']} {o['name']}**：{o.get('reason') or ''} {st}".rstrip())
            L += [f"  - {w}" for w in (o.get("why") or [])]
    else:
        L.append("不動作，持股續抱。")
    return "\n".join(L) + "\n"


def week_md(w: dict) -> str:
    if not w.get("ready"):
        return "# 百萬擂台週報\n\n還沒有資料。\n"
    g = w["grade"]
    L = [f"# 百萬擂台週報 {w['start']} ～ {w['end']}（{w['days']} 個交易日・{w['phase']}）", "",
         "## 成績", "",
         "| 項目 | 數值 |", "|---|---:|",
         f"| 週初總資產 | {_n(w['nav_start'])} |", f"| 週末總資產 | {_n(w['nav_end'])} |",
         f"| 本週損益 | {_s(w['pnl'], 0, ' 元')} |", f"| 本週報酬 | {_s(w['ret'])} |",
         f"| 同期 0050 | {_s(w['bench_ret'])} |", f"| 贏 / 輸 0050 | {_s(w['excess'])} |",
         f"| 本週最大回檔 | {_n(w['mdd'], 1)}% |", f"| 本週評分 | {g.get('letter', '—')}（{_n(g.get('score'))} 分） |",
         f"| 開賽至今 | {_s(w['total_ret'])} |", "",
         "## 每天", "", "| 日期 | 總資產 | 擂台 | 0050 | 買 | 賣 | 持股 |", "|---|---:|---:|---:|---:|---:|---:|"]
    L += [f"| {d['date']}（{d['weekday']}） | {_n(d['nav'])} | {_s(d['ret'])} | {_s(d['bench_ret'])} | {d['buys']} | {d['sells']} | {d['holdings']} |"
          for d in w["daily"]]
    L += ["", "## 交易統計", "",
          f"- 買進 {w['n_buy']} 筆、賣出 {w['n_sell']} 筆；賺 {w['wins']} 筆、賠 {w['losses']} 筆，勝率 {'—' if w['win_rate'] is None else _n(w['win_rate'], 0) + '%'}",
          f"- 平均賺 {_s(w['avg_win'], 1)}、平均賠 {_s(w['avg_loss'], 1)}",
          f"- 已實現損益 {_s(w['realized'], 0, ' 元')}；持股未實現 {_s(w['unrealized'], 0, ' 元')}；手續費＋證交稅 {_n(w['fees'])} 元"]
    if w["best"]:
        L.append(f"- 最好一筆：{w['best']['code']} {w['best']['name']} {_s(w['best']['pnl_pct'], 1)}（{w['best']['reason']}）")
    if w["worst"] and w["n_sell"] > 1:
        L.append(f"- 最差一筆：{w['worst']['code']} {w['worst']['name']} {_s(w['worst']['pnl_pct'], 1)}（{w['worst']['reason']}）")
    if w["exit_kinds"]:
        L += ["", "## 出場原因", "", "| 原因 | 筆數 | 損益 | 平均報酬 |", "|---|---:|---:|---:|"]
        L += [f"| {k['kind']} | {k['n']} | {_s(k['pnl'], 0, '')} | {_s(k['avg_pct'], 1)} |" for k in w["exit_kinds"]]
    L += ["", "## 全部成交", ""]
    if w["trades"]:
        L += ["| 日期 | 動作 | 股票 | 成交價 | 股數 | 金額 | 費用 | 損益 | 理由 |", "|---|---|---|---:|---:|---:|---:|---:|---|"]
        L += [f"| {t['date']} | {'買' if t['side'] == 'buy' else '賣'} | {t['code']} {t['name']} | {t['price']:.2f} | {_lots(t['shares'])} | "
              f"{_n(t['amount'])} | {_n(t['fee'] + t['tax'])} | {'' if t['pnl'] is None else _s(t['pnl'], 0, '') + '（' + _s(t['pnl_pct'], 1) + '）'} | {t['reason']} |"
              for t in w["trades"]]
    else:
        L.append("本週沒有成交。")
    if w["missed"]:
        L += ["", "## 沒有成交的委託", ""]
        L += [f"- {o['exec_date']} {'買進' if o['side'] == 'buy' else '賣出'} {o['code']} {o['name']}：{o['status']}" for o in w["missed"]]
    L += ["", f"## 週末持股（{len(w['holdings'])} 檔）", ""]
    if w["holdings"]:
        L += ["| 股票 | 買進日 | 成本 | 收盤 | 報酬 | 持有 | 買進理由 |", "|---|---|---:|---:|---:|---:|---|"]
        L += [f"| {h['code']} {h['name']} | {h['entry']} | {h['avg']:.2f} | {h['close']:.2f} | {_s(h['pnl_pct'], 1)} | {h['days']} 日 | {h['reason']} |"
              for h in w["holdings"]]
    else:
        L.append("空手。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 自動存檔
def log_dir(settings) -> Path:
    return Path(settings.db_path).parent / "擂台紀錄"


def export(store, folder: Path) -> Path | None:
    """把每天的日誌、每週週報、成交／委託／淨值 CSV 寫到 folder/開賽YYYY-MM-DD/。"""
    j = Journal(store)
    if not j.ready:
        return None
    root = Path(folder) / f"開賽{j.state['start']}"
    (root / "每日").mkdir(parents=True, exist_ok=True)
    (root / "每週").mkdir(parents=True, exist_ok=True)
    for d in j.days:
        _write(root / "每日" / f"{d}.md", day_md(j.day(d)))
    for wk in j.weeks():
        _write(root / "每週" / f"{wk}_週報.md", week_md(j.week(wk)))
    _csv(root / "成交紀錄.csv", j.trades, {"date": "日期", "code": "代號", "name": "名稱", "side": "動作",
                                       "shares": "股數", "price": "成交價", "amount": "金額", "fee": "手續費",
                                       "tax": "證交稅", "pnl": "損益", "pnl_pct": "報酬(%)", "reason": "理由"})
    _csv(root / "委託紀錄.csv", j.orders, {"decided": "決定日", "exec_date": "執行日", "side": "動作", "code": "代號",
                                       "name": "名稱", "status": "結果", "price": "開盤價", "score": "評分",
                                       "reason": "理由", "why": "細節"})
    _csv(root / "每日淨值.csv", j.nav, {"date": "日期", "cash": "現金", "value": "股票市值", "nav": "總資產",
                                    "bench": "0050 同額", "phase": "階段", "holdings": "持股檔數"})
    return root


def _write(path: Path, text: str) -> None:
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    except PermissionError:   # 使用者剛好用 Excel／記事本開著：這次先跳過，下次再寫
        pass


def _csv(path: Path, df: pd.DataFrame, cols: dict) -> None:
    out = df[[c for c in cols if c in df.columns]].rename(columns=cols)
    if "動作" in out.columns:
        out["動作"] = out["動作"].map({"buy": "買", "sell": "賣"}).fillna(out["動作"])
    if "階段" in out.columns:
        out["階段"] = out["階段"].map({"live": "實戰", "backtest": "回測"}).fillna(out["階段"])
    try:
        out.to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    except PermissionError:
        pass
