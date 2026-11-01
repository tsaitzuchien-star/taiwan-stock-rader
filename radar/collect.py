"""盤後批次收集器：把 TWSE / TPEx 全市場日 K 存進本地資料庫。

第一次執行會回補約 90 個交易日（每個市場每天 1 個請求，約 5～10 分鐘）；
之後每天只需補最新 1 天，幾秒鐘完成。

用法（在專案資料夾執行）：
    python -m radar.collect            # 補齊到最新
    python -m radar.collect --days 120 # 指定保留交易日數
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .collectors import chips
from .collectors.exchanges import FETCHERS, NoData
from .collectors.http import FetchError, Http
from .config import Settings, load_settings
from .storage import Store

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]
MAX_CONSECUTIVE_ERRORS = 5


@dataclass
class CollectReport:
    fetched: dict[str, int] = field(default_factory=dict)      # market -> 新抓的交易日數
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{m} 新增 {n} 份" if m == "籌碼" else f"{m} 新增 {n} 天" for m, n in self.fetched.items()]
        if self.errors:
            parts.append(f"{len(self.errors)} 個錯誤")
        return "，".join(parts) or "沒有需要更新的資料"


def candidate_days(today: date, trading_days: int) -> list[date]:
    """由新到舊的平日清單；台股一年約 245 個交易日，乘 1.6 再加緩衝足以涵蓋連假。"""
    span = int(trading_days * 1.6) + 15
    days = [today - timedelta(days=i) for i in range(span)]
    return [d for d in days if d.weekday() < 5]


def update(store: Store, settings: Settings, http: Http | None = None,
           today: date | None = None, progress: Progress | None = None,
           fetchers=None, chip_fetchers=None) -> CollectReport:
    http = http or Http(min_interval=settings.request_interval_sec)
    today = today or date.today()
    fetchers = fetchers or FETCHERS
    progress = progress or (lambda msg, frac: log.info(msg))
    report = CollectReport()
    days = candidate_days(today, settings.history_trading_days)
    markets = list(fetchers)

    for m_idx, market in enumerate(markets):
        fetch = fetchers[market]
        have = 0
        consecutive_errors = 0
        report.fetched[market] = 0
        for d_idx, day in enumerate(days):
            if have >= settings.history_trading_days:
                break
            frac = (m_idx + d_idx / len(days)) / len(markets)
            status = store.fetch_status(market, day)
            if status == "ok":
                have += 1
                continue
            if status == "holiday" and day < today:
                continue
            progress(f"下載 {market} {day.isoformat()} 全市場行情…", frac)
            try:
                quotes = fetch(http, day)
            except NoData as e:
                # 今天的資料可能還沒公布（約 14:30 後），所以今天不標記為休市
                store.log_fetch(market, day, "holiday" if day < today else "nodata", 0, str(e))
                consecutive_errors = 0
                continue
            except (FetchError, ValueError) as e:
                store.log_fetch(market, day, "error", 0, str(e))
                report.errors.append(f"{market} {day}: {e}")
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    report.errors.append(f"{market} 連續 {consecutive_errors} 次失敗，先停止，請檢查網路")
                    break
                continue
            consecutive_errors = 0
            store.upsert_stocks((q.code, q.name, market) for q in quotes)
            n = store.upsert_bars(q.bar_row(day) for q in quotes if q.close is not None)
            store.log_fetch(market, day, "ok", n)
            report.fetched[market] += 1
            have += 1

    store.prune_bars(settings.history_trading_days)
    # 測試只換掉日 K 下載器時，不去碰籌碼資料
    if chip_fetchers is not False and (chip_fetchers is not None or fetchers is FETCHERS):
        update_chips(store, settings, http, today, progress, report, chip_fetchers or None)
    progress("更新完成：" + report.summary(), 1.0)
    return report


def update_chips(store: Store, settings: Settings, http: Http, today: date,
                 progress: Progress, report: CollectReport, fetchers: dict | None = None) -> None:
    """法人籌碼與基本面。交易日直接用資料庫裡已有日 K 的日期，不必猜休市日。"""
    f = fetchers or {"inst": chips.fetch_inst, "margin": chips.fetch_margin,
                     "valuation": chips.fetch_valuation, "revenue": chips.fetch_revenue}
    days = [date.fromisoformat(d) for d in store.trading_days()]
    if not days:
        return
    recent = list(reversed(days[-settings.chip_trading_days:]))
    jobs: list[tuple[str, str, date | None]] = []
    for market in ("TWSE", "TPEX"):
        for ds in ("inst", "margin"):
            jobs += [(market, ds, d) for d in recent]
        jobs.append((market, "valuation", days[-1]))
        jobs.append((market, "revenue", None))
    names = {"inst": "三大法人", "margin": "融資融券", "valuation": "本益比", "revenue": "月營收"}
    failed: dict[str, int] = {}
    report.fetched.setdefault("籌碼", 0)
    for i, (market, ds, day) in enumerate(jobs):
        key = f"{market}:{ds}"
        log_day = day or today          # 月營收每天最多抓一次
        if store.fetch_status(key, log_day) in ("ok", "holiday"):
            continue
        if failed.get(key, 0) >= 3:      # 同一種資料連錯 3 次就先跳過，不拖慢整體
            continue
        progress(f"下載 {market} {names[ds]} {day or ''}…", 0.9 + 0.1 * i / len(jobs))
        try:
            if ds == "revenue":
                rev, ind = f[ds](http, market)
                n = store.upsert_dataset("revenue", rev)
                store.upsert_dataset("company", ind)
            else:
                n = store.upsert_dataset(ds, f[ds](http, market, day))
        except NoData as e:
            store.log_fetch(key, log_day, "holiday" if log_day < today else "nodata", 0, str(e))
            continue
        except Exception as e:  # noqa: BLE001  單一資料失敗不影響其他資料
            store.log_fetch(key, log_day, "error", 0, str(e))
            report.errors.append(f"{market} {names[ds]} {day or ''}: {e}")
            failed[key] = failed.get(key, 0) + 1
            continue
        store.log_fetch(key, log_day, "ok", n)
        report.fetched["籌碼"] += 1
    store.prune_dated("inst_flows", settings.chip_trading_days)
    store.prune_dated("margin", settings.chip_trading_days)
    store.prune_dated("valuation", 5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下載 TWSE / TPEx 全市場日 K 到本地資料庫")
    parser.add_argument("--days", type=int, help="保留的交易日數（預設 90）")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = load_settings()
    if args.days:
        settings.history_trading_days = args.days
    store = Store(settings.db_path)
    started = datetime.now()
    report = update(store, settings)
    h = store.health()
    print(f"{report.summary()}（耗時 {(datetime.now() - started).seconds} 秒）")
    print(f"資料庫最新日期 {h['latest_date']}，共 {h['trading_days']} 個交易日，"
          f"{h['stocks']} 檔股票，最新一日有 {h['stocks_on_latest']} 檔")
    for err in report.errors:
        print("錯誤：", err)
    return 1 if report.errors and not any(report.fetched.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
