// 台股雷達 戰情室前端（Preact + htm，不需要編譯）。台股慣例：紅漲綠跌。
import { h, render } from 'preact';
import { useState, useEffect, useRef, useMemo } from 'preact/hooks';
import htm from 'htm';
import { createChart, CandlestickSeries, LineSeries, HistogramSeries, CrosshairMode } from 'lightweight-charts';

const html = htm.bind(h);
const C = { up: '#FF5A5F', down: '#2BC48A', accent: '#F5A524', blue: '#5B8DEF', muted: '#8B95A8', line: '#1E2533', grid: '#161C28' };

// ---------------------------------------------------------------- 工具
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* 非 JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—'
  : Number(v).toLocaleString('zh-TW', { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 2, suffix = '') => (v === null || v === undefined) ? '—' : `${v > 0 ? '+' : ''}${fmt(v, d)}${suffix}`;
const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'dn' : '');

function useHash() {
  const [hash, setHash] = useState(location.hash || '#/overview');
  useEffect(() => {
    const on = () => setHash(location.hash || '#/overview');
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash.replace(/^#\/?/, '').split('/').map((x) => decodeURIComponent(x));
}

function Spark({ data, w = 84, h = 22 }) {
  if (!data || data.length < 2) return null;
  const lo = Math.min(...data), hi = Math.max(...data), span = hi - lo || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1) * w).toFixed(1)},${(h - 2 - (v - lo) / span * (h - 4)).toFixed(1)}`).join(' ');
  const color = data[data.length - 1] >= data[0] ? C.up : C.down;
  return html`<svg width=${w} height=${h} viewBox="0 0 ${w} ${h}" role="img" aria-label="近 20 日走勢">
    <polyline points=${pts} fill="none" stroke=${color} stroke-width="1.5" /></svg>`;
}

// ---------------------------------------------------------------- 外框
const NAV = [
  ['overview', '總覽'], ['arena', '百萬擂台'], ['alpha', '法人選股'], ['trust', '投信預測'], ['sectors', '產業輪動'], ['radar', '雷達掃描'], ['watch', '觀察名單'], ['stock', '個股分析'],
  ['positions', '持倉風控'], ['settings', '資料與設定'],
];

function TopBar({ health, breadth }) {
  const db = health?.db;
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  return html`<header class="topbar">
    <div class="brand">台股雷達<small>V12 戰情室</small></div>
    <div class="ticker">
      ${breadth?.latest && html`
        ${breadth.live ? html`<span class="chip live" title=${`元大即時報價 ${breadth.count} 檔 · ${breadth.time || ''} 更新；盤後資料庫最新 ${breadth.db_latest}`}>
            ${breadth.closed ? '今日收盤' : '盤中即時'} <b class="num">${(breadth.time || '').slice(0, 5)}</b></span>`
          : html`<span><span class="mut">資料日期</span> <b class="num">${breadth.latest}</b></span>`}
        <span><span class="mut">上漲</span> <b class="num up">${breadth.up}</b></span>
        <span><span class="mut">下跌</span> <b class="num dn">${breadth.down}</b></span>
        <span><span class="mut">站上季線</span> <b class="num">${fmt(breadth.above_ma60_pct, 1)}%</b></span>`}
    </div>
    <div class="right">
      <${YuantaChip} />
      <span class="chip num">${now.toLocaleTimeString('zh-TW', { hour12: false })}</span>
      ${db && html`<span class="mut">${db.stocks?.toLocaleString()} 檔 · ${db.trading_days} 日</span>`}
    </div>
  </header>`;
}

function YuantaChip() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    const load = () => api('/api/yuanta/status').then(setSt).catch(() => {});
    load();
    const t = setInterval(load, 10000);
    addEventListener('yuanta', load);
    return () => { clearInterval(t); removeEventListener('yuanta', load); };
  }, []);
  if (!st?.configured) return html`<span class="chip off" title="config.local.toml 尚未設定元大帳號">元大 未設定</span>`;
  if (st.logged_in) return html`<a class="chip ok" href="#/positions" title=${`${st.environment} ${st.account} · ${st.connected_at} 登入`}>● 元大 已連線</a>`;
  if (st.connecting) return html`<span class="chip">元大 連線中…</span>`;
  return html`<a class="chip off" href="#/positions" title=${st.error || '到持倉風控連線'}>元大 未連線</a>`;
}

function SideNav({ page, health }) {
  const db = health?.db;
  return html`<nav class="sidenav" aria-label="主選單">
    ${NAV.map(([id, label]) => html`<a href="#/${id}" class=${page === id ? 'on' : ''}>${label}</a>`)}
    <div class="foot">
      資料庫 <span class="num" style="color:var(--text-2)">${db?.latest_date || '尚無資料'}</span><br />
      ${db ? `${db.trading_days} 個交易日 · 最新 ${db.stocks_on_latest} 檔` : ''}
    </div>
  </nav>`;
}

// ---------------------------------------------------------------- 總覽
function Overview({ health, breadth }) {
  const [summary, setSummary] = useState(null);
  const [hits, setHits] = useState(null);
  const [alpha, setAlpha] = useState(null);
  const [flows, setFlows] = useState(null);
  const [sectors, setSectors] = useState(null);
  const [err, setErr] = useState('');
  const watch = useWatched();
  useEffect(() => {
    if (!health?.db?.latest_date) return;
    api('/api/tactics/summary').then(setSummary).catch((e) => setErr(e.message));
    post('/api/scan', { tactic: 'dive' }).then(setHits).catch((e) => setErr(e.message));
    api('/api/alpha?limit=10').then(setAlpha).catch((e) => setErr(e.message));
    api('/api/market/flows').then(setFlows).catch(() => {});
    api('/api/alpha/sectors').then(setSectors).catch(() => {});
  }, [health?.db?.latest_date]);
  if (health && !health.db.latest_date) return html`<${EmptyDb} />`;
  const max = Math.max(1, ...(summary || []).map((s) => s.count));
  const total = breadth ? breadth.up + breadth.down + breadth.flat : 0;
  return html`
    <section class="grid g-kpi">
      <div class="panel pb kpi"><div class="label">${breadth?.live ? (breadth.closed ? '今日收盤（元大）' : '盤中即時（元大）') : '資料日期'}</div>
        <div class="value">${breadth?.live ? (breadth.time || '').slice(0, 5) : breadth?.latest || '—'}</div>
        <div class="subv mut">${breadth?.live ? `${breadth.latest} · ${breadth.count} 檔每分鐘更新` : '官方盤後資料'}</div></div>
      <div class="panel pb kpi"><div class="label">上漲 / 下跌家數</div>
        <div class="value"><span class="up">${breadth?.up ?? '—'}</span> <span class="mut">/</span> <span class="dn">${breadth?.down ?? '—'}</span></div>
        <div class="subv mut">平盤 ${breadth?.flat ?? '—'}</div></div>
      <div class="panel pb kpi"><div class="label">漲停 / 跌停</div>
        <div class="value"><span class="up">${breadth?.limit_up ?? '—'}</span> <span class="mut">/</span> <span class="dn">${breadth?.limit_down ?? '—'}</span></div>
        <div class="subv mut">漲跌幅 ≥ 9.5%</div></div>
      <div class="panel pb kpi"><div class="label">站上季線比例</div>
        <div class="value">${breadth?.above_ma60_pct == null ? '—' : fmt(breadth.above_ma60_pct, 1) + '%'}</div>
        <div class="subv mut">收盤 ≥ 60 日均線</div></div>
    </section>
    ${err && html`<div class="toast">${err}</div>`}
    <${ArenaCard} />
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">法人綜合評分 前 10 名<span class="sub"><a href="#/alpha">看全部</a></span></div>
        ${alpha && !alpha.coverage.with_chips ? html`<${NoChips} />` : html`<${AlphaTable} rows=${(alpha?.rows || []).slice(0, 10)} compact watch=${watch} source="法人選股" date=${health?.db?.latest_date} />`}
      </div>
      <${FlowsPanel} flows=${flows} />
    </section>
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">雷達今日命中 · 深海潛艦<span class="sub">${hits ? `${hits.rows.length} 檔` : '計算中…'} · <a href="#/radar">看全部</a></span></div>
        <${HitTable} rows=${(hits?.rows || []).slice(0, 10)} compact watch=${watch} source="深海潛艦" date=${health?.db?.latest_date} />
      </div>
      <div class="panel">
        <div class="ph">產業資金流向<span class="sub">法人近 5 日淨買超 · <a href="#/sectors">產業輪動</a></span></div>
        <${SectorFlowBars} sectors=${sectors} />
      </div>
    </section>
    <section class="grid g-3">
      <div class="panel">
        <div class="ph">市場廣度<span class="sub">上市＋上櫃普通股</span></div>
        <div class="pb" style="display:flex;flex-direction:column;gap:12px;font-size:13px">
          ${total > 0 && html`<div class="bar">
            <div style=${`width:${breadth.up / total * 100}%;background:${C.up}`}></div>
            <div style=${`width:${breadth.flat / total * 100}%;background:#3A4256`}></div>
            <div style=${`width:${breadth.down / total * 100}%;background:${C.down}`}></div></div>`}
          <div style="display:flex;justify-content:space-between"><span class="up num">上漲 ${breadth?.up ?? '—'}</span>
            <span class="mut num">平盤 ${breadth?.flat ?? '—'}</span><span class="dn num">下跌 ${breadth?.down ?? '—'}</span></div>
          <div style="display:flex;justify-content:space-between"><span class="mut">站上季線比例</span><span class="num">${fmt(breadth?.above_ma60_pct, 1)}%</span></div>
        </div>
      </div>
      <div class="panel">
        <div class="ph">四大戰術命中<span class="sub">全市場</span></div>
        <div class="pb" style="display:flex;flex-direction:column;gap:12px;font-size:13px">
          ${(summary || []).map((s) => html`<a href=${`#/radar/${s.id}`} style="display:flex;align-items:center;gap:10px;color:var(--text)">
            <span style="flex:0 0 84px">${s.name}</span>
            <div class="meter"><div style=${`width:${s.count / max * 100}%`}></div></div>
            <span class="num" style="flex:0 0 36px;text-align:right">${s.count}</span></a>`)}
          ${!summary && html`<span class="mut">計算中…</span>`}
        </div>
      </div>
      <div class="panel">
        <div class="ph">持倉警示<span class="sub">元大即時 · <a href="#/positions">持倉風控</a></span></div>
        <${PositionAlerts} />
      </div>
    </section>`;
}

// ---------------------------------------------------------------- 百萬操盤擂台
function useArena() {
  const [a, setA] = useState(null);
  const [err, setErr] = useState('');
  const load = () => api('/api/arena').then((r) => { setA(r); setErr(''); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {   // 第一次開賽在背景回測：每 3 秒看一下好了沒
    if (!a?.running) return undefined;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [a]);
  return [a, err, load, setA];
}

const won = (v) => (v == null ? '—' : `${Math.round(v).toLocaleString('zh-TW')}`);

function Grade({ g, big }) {
  const letter = g?.letter || '—';
  return html`<span class=${`grade g-${letter === '—' ? 'na' : letter}${big ? ' big' : ''}`} title="操盤評分">
    <b>${letter}</b>${g?.score != null && html`<small class="num">${g.score} 分</small>`}</span>`;
}

function CurveSpark({ curve, w = 260, h = 64 }) {
  if (!curve || curve.length < 2) return null;
  const vals = curve.flatMap((p) => [p.nav, p.bench].filter((v) => v != null));
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const xy = (i, v) => `${(i / (curve.length - 1) * w).toFixed(1)},${(h - 3 - (v - lo) / span * (h - 6)).toFixed(1)}`;
  const line = (k) => curve.map((p, i) => (p[k] == null ? null : xy(i, p[k]))).filter(Boolean).join(' ');
  return html`<svg class="cspark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="擂台淨值與 0050 走勢">
    <polyline points=${line('bench')} fill="none" stroke=${C.blue} stroke-width="1.2" stroke-dasharray="3 3" vector-effect="non-scaling-stroke" />
    <polyline points=${line('nav')} fill="none" stroke=${C.accent} stroke-width="2" vector-effect="non-scaling-stroke" /></svg>`;
}

function ArenaWaiting({ a, err }) {
  if (err) return html`<div class="empty">擂台暫時讀不到：${err}</div>`;
  if (!a) return html`<div class="empty">讀取中…</div>`;
  if (a.running) return html`<div class="empty">第一次開賽：正在用最近幾週的資料回測，約需 1 分鐘…</div>`;
  if (a.error) return html`<div class="empty">擂台計算失敗：${a.error}</div>`;
  return html`<div class="empty">資料庫至少要 61 個交易日的日 K 才能開賽（要算季線）。請先到「資料與設定」更新資料庫。</div>`;
}

function ArenaCard() {
  const [a, err] = useArena();
  const ready = a?.ready;
  const buys = (a?.orders || []).filter((o) => o.side === 'buy');
  const sells = (a?.orders || []).filter((o) => o.side === 'sell');
  return html`<section class="panel arena-card">
    <div class="ph">🏆 ${a?.name || '百萬操盤擂台'}<span class="sub">模擬操盤 100 萬，不是真的下單 · <a href="#/arena">看完整成績</a></span></div>
    ${!ready ? html`<${ArenaWaiting} a=${a} err=${err} />` : html`<a class="ac-body" href="#/arena">
      <div class="ac-kpi"><div class="label">目前總資產</div><div class="value num">${won(a.nav)}</div>
        <div class=${`subv num ${cls(a.total_ret)}`}>${signed(a.total_ret, 2, '%')}（${signed(a.nav - a.start_cash, 0)} 元）</div></div>
      <div class="ac-kpi"><div class="label">同期 0050</div><div class=${`value num ${cls(a.bench_total)}`}>${signed(a.bench_total, 2, '%')}</div>
        <div class=${`subv num ${cls(a.excess)}`}>${a.excess == null ? '—' : `${a.excess >= 0 ? '贏' : '輸'} ${fmt(Math.abs(a.excess), 2)}%`}</div></div>
      <div class="ac-kpi"><div class="label">操盤評分</div><div class="value"><${Grade} g=${a.grade} /></div>
        <div class="subv mut">${a.trading_days} 個交易日 · 勝率 ${a.win_rate == null ? '—' : fmt(a.win_rate, 0) + '%'}</div></div>
      <div class="ac-curve"><${CurveSpark} curve=${a.curve} />
        <div class="legend"><span><i style=${`background:${C.accent}`}></i>擂台</span><span><i style=${`background:${C.blue}`}></i>0050</span></div></div>
      <div class="ac-orders"><div class="label">明天開盤</div>
        ${!buys.length && !sells.length ? html`<div class="mut">不動作，續抱 ${a.positions.length} 檔</div>` : html`
          ${buys.slice(0, 4).map((o) => html`<div><span class="tag up">買</span>${o.code} ${o.name}</div>`)}
          ${sells.slice(0, 4).map((o) => html`<div><span class="tag dn">賣</span>${o.code} ${o.name}</div>`)}`}
      </div></a>`}
  </section>`;
}

function EquityChart({ curve }) {
  const el = useRef(null);
  useEffect(() => {
    if (!el.current || !curve?.length) return undefined;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: C.muted, fontFamily: 'Consolas, monospace' },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.line }, timeScale: { borderColor: C.line },
      localization: { locale: 'zh-TW', dateFormat: 'yyyy-MM-dd', priceFormatter: (v) => Math.round(v).toLocaleString('zh-TW') },
    });
    const bench = chart.addSeries(LineSeries, { color: C.blue, lineWidth: 1.5, lineStyle: 2, priceLineVisible: false, title: '0050' });
    bench.setData(curve.filter((p) => p.bench != null).map((p) => ({ time: p.date, value: p.bench })));
    const nav = chart.addSeries(LineSeries, { color: C.accent, lineWidth: 2.5, priceLineVisible: false, title: '擂台' });
    nav.setData(curve.map((p) => ({ time: p.date, value: p.nav })));
    const base = nav.createPriceLine({ price: 1_000_000, color: '#3A4458', lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: '本金' });
    chart.timeScale().fitContent();
    return () => { nav.removePriceLine(base); chart.remove(); };
  }, [curve]);
  return html`<div class="chart eq" ref=${el}></div>`;
}

function ArenaPage({ health }) {
  const [a, err, load, setA] = useArena();
  const [busy, setBusy] = useState(false);
  const reset = async (fresh) => {
    const msg = fresh
      ? `確定要從今天重新開賽嗎？\n目前的成績會清掉，100 萬從資料庫最新一天（${health?.db?.latest_date || ''}）收盤開始，下一個交易日開盤第一次買進。\n\n提醒：請先在收盤後「更新資料庫」，讓今天的資料進來再按。`
      : '確定要重新開賽嗎？目前的成績會清掉，用最近幾週的資料重新回測。';
    if (!confirm(msg)) return;
    setBusy(true);
    try { setA(await post(`/api/arena/reset${fresh ? '?fresh=true' : ''}`)); } catch (e) { alert(e.message); }
    setBusy(false);
  };
  if (health && !health.db.latest_date) return html`<${EmptyDb} />`;
  const rules = a?.rules;
  const go = (code) => { location.hash = `#/stock/${code}`; };
  return html`
    <div class="panel"><div class="ph">🏆 ${a?.name || '百萬操盤擂台'}
      <span class="sub">雷達用法人評分每天模擬操作 100 萬元（不是真的下單）·
        ${a?.ready ? html`開賽 ${a.start}${a.fresh ? ' · 全程實戰（沒有回測）' : a.live_since ? ` · ${a.live_since} 起為實戰` : ` · ${a.last} 以前為回測，下一個交易日起實戰`}` : ''}</span></div>
      ${!a?.ready && html`<${ArenaWaiting} a=${a} err=${err} />`}
    </div>
    ${a?.ready && html`
    <section class="grid g-kpi">
      <div class="panel pb kpi"><div class="label">目前總資產</div><div class="value">${won(a.nav)}</div>
        <div class="subv mut">現金 ${won(a.cash)}</div></div>
      <div class="panel pb kpi"><div class="label">總報酬</div><div class=${`value ${cls(a.total_ret)}`}>${signed(a.total_ret, 2, '%')}</div>
        <div class=${`subv ${cls(a.total_ret)}`}>${signed(a.nav - a.start_cash, 0)} 元</div></div>
      <div class="panel pb kpi"><div class="label">同期 0050</div><div class=${`value ${cls(a.bench_total)}`}>${signed(a.bench_total, 2, '%')}</div>
        <div class=${`subv ${cls(a.excess)}`}>超額 ${signed(a.excess, 2, '%')}</div></div>
      <div class="panel pb kpi"><div class="label">操盤評分</div><div class="value"><${Grade} g=${a.grade} big /></div>
        <div class="subv mut">最大回檔 ${fmt(a.mdd, 1)}%</div></div>
      <div class="panel pb kpi"><div class="label">已出場 / 勝率</div><div class="value">${a.closed} <span class="mut" style="font-size:16px">筆</span></div>
        <div class="subv mut">勝率 ${a.win_rate == null ? '—' : fmt(a.win_rate, 0) + '%'}${a.live_ret != null ? ` · 實戰 ${signed(a.live_ret, 2, '%')}` : ''}</div></div>
    </section>
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">資產曲線<span class="sub"><span style=${`color:${C.accent}`}>━ 擂台</span>　<span style=${`color:${C.blue}`}>┅ 0050</span>　起點都是 100 萬</span></div>
        <${EquityChart} curve=${a.curve} />
      </div>
      <div class="panel">
        <div class="ph">成績單<span class="sub">以交易日計：1 週 = 5 天、1 個月 = 20 天</span></div>
        <div class="tablewrap"><table>
          <thead><tr><th class="l">期間</th><th>擂台</th><th>0050</th><th>贏 / 輸</th><th>最大回檔</th><th>評分</th></tr></thead>
          <tbody>${a.periods.map((p) => html`<tr class="nohover">
            <td class="l">${p.label}</td>
            ${p.ret == null ? html`<td colspan="5" class="mut">還沒滿 ${p.days} 個交易日</td>` : html`
              <td class=${`num ${cls(p.ret)}`}>${signed(p.ret, 2, '%')}</td>
              <td class=${`num ${cls(p.bench)}`}>${signed(p.bench, 2, '%')}</td>
              <td class=${`num ${cls(p.excess)}`}>${signed(p.excess, 2, '%')}</td>
              <td class="num mut">${fmt(p.mdd, 1)}%</td>
              <td><${Grade} g=${p.grade} /></td>`}</tr>`)}</tbody>
        </table></div>
        <div class="pb mut small">評分 = 60 分起跳，每贏 0050 1% 加 4 分；最大回檔超過 5% 的部分每 1% 扣 1.5 分。S ≥ 90、A ≥ 75、B ≥ 60、C ≥ 45。</div>
      </div>
    </section>
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">目前持股<span class="sub">${a.positions.length} / 8 檔 · 收盤價計算</span></div>
        ${!a.positions.length ? html`<div class="empty">目前空手，等符合條件的股票出現。</div>` : html`<div class="tablewrap"><table>
          <thead><tr><th class="l">股票</th><th>股數</th><th>成本</th><th>現價</th><th>市值</th><th>損益</th><th>比重</th><th>持有</th></tr></thead>
          <tbody>${a.positions.map((p) => html`<tr onClick=${() => go(p.code)}>
            <td class="l"><span class="num acc">${p.code}</span> ${p.name}</td>
            <td class="num">${p.shares.toLocaleString()}</td><td class="num">${fmt(p.avg, 2)}</td><td class="num">${fmt(p.last, 2)}</td>
            <td class="num">${won(p.value)}</td>
            <td class=${`num ${cls(p.pnl)}`}>${signed(p.pnl, 0)}<br /><small>${signed(p.pnl_pct, 1, '%')}</small></td>
            <td class="num mut">${fmt(p.weight, 1)}%</td><td class="num mut">${p.days} 日</td></tr>
            <tr class="why nohover"><td colspan="8" class="l">
              <div class="buyline">📌 <b>${p.entry}</b> 09:00 開盤以 <b class="num">${fmt(p.entry_price, 2)}</b> 元買進
                <b class="num">${p.shares.toLocaleString()}</b> 股（${p.shares >= 1000 ? `${Math.floor(p.shares / 1000)} 張${p.shares % 1000 ? ` ${p.shares % 1000} 股` : ''}` : '零股'}），
                花費 <b class="num">${won(p.entry_price * p.shares + (p.entry_fee || 0))}</b> 元（含手續費）</div>
              ${p.why?.length ? html`<ul class="whylist">${p.why.map((w) => html`<li>${w}</li>`)}</ul>`
                : html`<div class="mut">買進理由：${p.reason}</div>`}
            </td></tr>`)}</tbody>
        </table></div>`}
      </div>
      <div class="panel">
        <div class="ph">明天開盤要做的事<span class="sub">${a.last} 收盤後決定，照開盤價成交</span></div>
        ${!a.orders.length ? html`<div class="empty">明天不動作，持股續抱。</div>` : html`<div class="tablewrap"><table>
          <thead><tr><th class="l">動作</th><th class="l">股票</th><th class="l">理由</th><th>預計金額</th></tr></thead>
          <tbody>${a.orders.map((o) => html`<tr onClick=${() => go(o.code)}>
            <td class="l"><span class=${`tag ${o.side === 'buy' ? 'up' : 'dn'}`}>${o.side === 'buy' ? '買進' : '賣出'}</span></td>
            <td class="l"><span class="num acc">${o.code}</span> ${o.name}</td>
            <td class="l mut">${o.why?.length ? html`<ul class="whylist">${o.why.map((w) => html`<li>${w}</li>`)}</ul>` : o.reason}</td>
            <td class="num">${o.side === 'buy' ? won(o.budget) : '全部'}</td></tr>`)}</tbody>
        </table></div>`}
      </div>
    </section>
    <div class="panel">
      <div class="ph">交易紀錄<span class="sub">最近 60 筆 · 已扣手續費與證交稅</span></div>
      ${!a.recent_trades.length ? html`<div class="empty">還沒有成交。</div>` : html`<div class="tablewrap"><table>
        <thead><tr><th class="l">日期</th><th class="l">動作</th><th class="l">股票</th><th>股數</th><th>成交價</th><th>金額</th><th>費用</th><th>損益</th><th class="l">理由</th></tr></thead>
        <tbody>${a.recent_trades.map((t) => html`<tr onClick=${() => go(t.code)}>
          <td class="l num mut">${t.date}</td>
          <td class="l"><span class=${`tag ${t.side === 'buy' ? 'up' : 'dn'}`}>${t.side === 'buy' ? '買' : '賣'}</span></td>
          <td class="l"><span class="num acc">${t.code}</span> ${t.name}</td>
          <td class="num">${t.shares.toLocaleString()}</td><td class="num">${fmt(t.price, 2)}</td><td class="num">${won(t.amount)}</td>
          <td class="num mut">${won(t.fee + t.tax)}</td>
          <td class=${`num ${cls(t.pnl)}`}>${t.pnl == null ? '' : html`${signed(t.pnl, 0)} <small>${signed(t.pnl_pct, 1, '%')}</small>`}</td>
          <td class="l mut">${t.reason}</td></tr>`)}</tbody>
      </table></div>`}
    </div>`}
    ${rules && html`<div class="panel">
      <div class="ph">操盤守則<span class="sub">規則固定、每天照表操課，不看盤中、不憑感覺
        ${a?.ready && html` · <button class="linkbtn" disabled=${busy || a.running} onClick=${() => reset(true)}>從今天重新開賽</button>
          · <button class="linkbtn" disabled=${busy || a.running} onClick=${() => reset(false)}>重新回測</button>`}</span></div>
      <div class="grid g-3 pb rules">
        <div><h4 class="up">何時買</h4><ul>${rules.entry.map((x) => html`<li>${x}</li>`)}</ul></div>
        <div><h4 class="dn">何時賣</h4><ul>${rules.exit.map((x) => html`<li>${x}</li>`)}</ul></div>
        <div><h4 class="acc">怎麼成交</h4><ul>${rules.execution.map((x) => html`<li>${x}</li>`)}</ul></div>
      </div>
    </div>`}`;
}

function EmptyDb() {
  return html`<div class="panel"><div class="empty">資料庫還是空的。<br />請到 <a href="#/settings">資料與設定</a> 按「更新資料庫」，第一次約 5～10 分鐘。</div></div>`;
}

function NoChips() {
  return html`<div class="empty">還沒有法人籌碼資料。<br />請到 <a href="#/settings">資料與設定</a> 按「更新資料庫」，會一併下載三大法人、融資融券、本益比與月營收（第一次約多 4 分鐘）。</div>`;
}

// ---------------------------------------------------------------- 法人籌碼元件
const yi = (v) => `${v > 0 ? '+' : ''}${fmt(v, 1)}`;

function FlowsPanel({ flows }) {
  const last = flows?.[flows.length - 1];
  const days = (flows || []).slice(-10);
  const max = Math.max(1, ...days.map((d) => Math.abs(d.foreign + d.trust + d.dealer)));
  return html`<div class="panel">
    <div class="ph">三大法人買賣超<span class="sub">億元（以收盤價估算）${last ? ` · ${last.date}` : ''}</span></div>
    ${!flows ? html`<div class="empty">計算中…</div>` : !flows.length ? html`<${NoChips} />` : html`<div class="pb">
      <div class="grid" style="grid-template-columns:repeat(4,minmax(0,1fr));gap:8px">
        ${[['外資', last.foreign], ['投信', last.trust], ['自營商', last.dealer], ['合計', last.foreign + last.trust + last.dealer]].map(([n, v]) => html`
          <div><div class="mut" style="font-size:12px">${n}</div><div class=${`num ${cls(v)}`} style="font-size:20px;font-weight:600;margin-top:4px">${yi(v)}</div></div>`)}
      </div>
      <div class="mut" style="font-size:12px;margin:16px 0 6px">近 ${days.length} 日合計</div>
      <div class="flowbars">${days.map((d) => {
        const v = d.foreign + d.trust + d.dealer;
        return html`<div class="fb" title=${`${d.date} 合計 ${yi(v)} 億（外資 ${yi(d.foreign)}、投信 ${yi(d.trust)}、自營 ${yi(d.dealer)}）`}>
          <div class="fb-up">${v > 0 && html`<i style=${`height:${v / max * 100}%;background:${C.up}`}></i>`}</div>
          <div class="fb-dn">${v < 0 && html`<i style=${`height:${-v / max * 100}%;background:${C.down}`}></i>`}</div>
          <span>${d.date.slice(5)}</span></div>`;
      })}</div>
    </div>`}
  </div>`;
}

function SectorFlowBars({ sectors }) {
  if (!sectors) return html`<div class="empty">計算中…</div>`;
  const list = [...sectors].filter((x) => x.industry !== '其他').sort((a, b) => Math.abs(b.legal5) - Math.abs(a.legal5)).slice(0, 10);
  if (!list.length || list.every((x) => !x.legal5)) return html`<${NoChips} />`;
  const max = Math.max(...list.map((x) => Math.abs(x.legal5)), 0.1);
  return html`<div class="pb" style="display:flex;flex-direction:column;gap:9px;font-size:13px">
    ${list.map((x) => html`<a class="sfb" href=${`#/alpha/all/${encodeURIComponent(x.industry)}`}>
      <span class="sfb-n">${x.industry}</span>
      <span class="sfb-l">${x.legal5 < 0 && html`<i style=${`width:${-x.legal5 / max * 100}%;background:${C.down}`}></i>`}</span>
      <span class="sfb-r">${x.legal5 > 0 && html`<i style=${`width:${x.legal5 / max * 100}%;background:${C.up}`}></i>`}</span>
      <span class=${`num ${cls(x.legal5)}`} style="flex:0 0 64px;text-align:right">${yi(x.legal5)}</span></a>`)}
  </div>`;
}

function Score({ v, big }) {
  if (v === null || v === undefined) return html`<span class="mut">—</span>`;
  const color = v >= 70 ? C.up : v >= 50 ? C.accent : C.muted;
  return html`<span class=${`score ${big ? 'big' : ''}`}><span class="num" style=${`color:${color}`}>${fmt(v, 0)}</span>
    <span class="meter"><span style=${`width:${v}%;background:${color}`}></span></span></span>`;
}

function Factors({ r }) {
  return html`<span class="factors" title=${`籌碼 ${fmt(r.s_chip, 0)}、動能 ${fmt(r.s_momentum, 0)}、成長 ${fmt(r.s_growth, 0)}、估值 ${fmt(r.s_value, 0)}`}>
    ${['s_chip', 's_momentum', 's_growth', 's_value'].map((k) => html`<i style=${`height:${Math.max(4, r[k] || 0)}%;background:${(r[k] || 0) >= 70 ? C.up : (r[k] || 0) >= 50 ? C.accent : '#3A4458'}`}></i>`)}
  </span>`;
}

const tagClass = (t) => (/連賣/.test(t) ? 'dn' : /土洋|投信|外資|爆發/.test(t) ? 'hot' : /退場/.test(t) ? 'up' : '');

const ACOLS = [
  ['rank', '#'], ['code', '代號', 'l'], ['name', '名稱', 'l'], ['industry', '產業', 'l'], ['score', '總分', 'l'], ['factors', '四因子', 'l', true],
  ['chg', '漲跌%'], ['f5', '外資5日(張)'], ['t5', '投信5日(張)'], ['t_streak', '投信連買'], ['legal5_ratio', '法人佔量%'],
  ['margin_chg5', '融資5日(張)'], ['yoy', '營收年增%'], ['pe', '本益比'], ['dy', '殖利率%'],
];

function AlphaTable({ rows, compact, sort, onSort, watch, source, date }) {
  if (!rows.length) return html`<div class="empty">沒有符合條件的股票。</div>`;
  const cols = compact ? ACOLS.filter(([k]) => ['rank', 'code', 'name', 'score', 'factors', 'f5', 't5'].includes(k)) : ACOLS;
  const cell = (r, k) => {
    switch (k) {
      case 'rank': return html`<td class="num mut">${r.rank}</td>`;
      case 'code': return html`<td class="l num acc">${r.code}</td>`;
      case 'name': return html`<td class="l">${r.name}</td>`;
      case 'industry': return html`<td class="l mut">${r.industry}</td>`;
      case 'score': return html`<td class="l"><${Score} v=${r.score} /></td>`;
      case 'factors': return html`<td class="l"><${Factors} r=${r} /></td>`;
      case 'chg': return html`<td class=${`num ${cls(r.chg)}`}>${signed(r.chg, 2)}</td>`;
      case 'f5': case 't5': case 'margin_chg5': return html`<td class=${`num ${cls(r[k])}`}>${r[k] == null ? '—' : signed(r[k], 0)}</td>`;
      case 't_streak': return html`<td class=${`num ${cls(r.t_streak)}`}>${r.t_streak == null ? '—' : r.t_streak > 0 ? `${r.t_streak} 日` : r.t_streak < 0 ? `賣 ${-r.t_streak}` : '0'}</td>`;
      case 'legal5_ratio': case 'yoy': return html`<td class=${`num ${cls(r[k])}`}>${r[k] == null ? '—' : signed(r[k], 1)}</td>`;
      default: return html`<td class="num">${fmt(r[k], k === 'pe' ? 1 : 2)}</td>`;
    }
  };
  return html`<div class="tablewrap"><table>
    <thead><tr>${cols.map(([k, label, al, nosort]) => html`<th class=${`${al || ''} ${onSort && !nosort ? 'sortable' : ''} ${sort?.key === k ? 'sorted' : ''}`}
      onClick=${() => onSort && !nosort && onSort(k)}>${label}${sort?.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>${k === 'name' && watch ? html`<th class="l">觀察</th>` : ''}`)}
      ${!compact && html`<th class="l">訊號</th>`}</tr></thead>
    <tbody>${rows.map((r) => html`<tr onClick=${() => { location.hash = `#/stock/${r.code}`; }}>
      ${cols.map(([k]) => html`${cell(r, k)}${k === 'name' && watch ? html`<td class="l"><${WatchBtn} code=${r.code} date=${date} source=${source} watch=${watch} /></td>` : ''}`)}
      ${!compact && html`<td class="l">${(r.tags || []).map((t) => html`<span class=${`tag ${tagClass(t)}`}>${t}</span>`)}</td>`}
    </tr>`)}</tbody></table></div>`;
}

function alphaCsv(rows) {
  const cols = ACOLS.filter(([, , , nosort]) => !nosort);
  const head = [...cols.map(([, l]) => l), '籌碼', '動能', '成長', '估值', '訊號'].join(',');
  const body = rows.map((r) => [...cols.map(([k]) => r[k] ?? ''), r.s_chip, r.s_momentum, r.s_growth, r.s_value, (r.tags || []).join(' ')].join(',')).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + head + '\n' + body], { type: 'text/csv;charset=utf-8' }));
  a.download = `法人選股_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

// ---------------------------------------------------------------- 法人選股
function Alpha({ preset: initPreset, industry: initIndustry, health }) {
  const [preset, setPreset] = useState(initPreset || 'all');
  const [industry, setIndustry] = useState(initIndustry || '');
  const [market, setMarket] = useState('');
  const [minVol, setMinVol] = useState(300);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sort, setSort] = useState(null);
  const watch = useWatched();
  useEffect(() => { setPreset(initPreset || 'all'); setIndustry(initIndustry || ''); }, [initPreset, initIndustry]);
  useEffect(() => {
    if (!health?.db?.latest_date) return;
    setBusy(true); setErr('');
    const q = new URLSearchParams({ preset, industry, market, min_vol20: minVol, limit: 500 });
    api(`/api/alpha?${q}`).then((r) => { setRes(r); setSort(null); }).catch((e) => setErr(e.message)).finally(() => setBusy(false));
  }, [preset, industry, market, minVol, health?.db?.latest_date]);
  const rows = useMemo(() => {
    if (!res) return [];
    if (!sort) return res.rows;
    const { key, dir } = sort;
    return [...res.rows].sort((a, b) => {
      const x = a[key], y = b[key];
      return (typeof x === 'string' ? x.localeCompare(y) : (x ?? -1e18) - (y ?? -1e18)) * dir;
    });
  }, [res, sort]);
  if (health && !health.db.latest_date) return html`<${EmptyDb} />`;
  const cov = res?.coverage;
  const pr = res?.presets.find((p) => p.id === preset);
  return html`
    <section class="panel pb" style="display:flex;flex-direction:column;gap:14px">
      <div class="presets">${(res?.presets || []).map((p) => html`<button type="button" class=${`pill ${p.id === preset ? 'on' : ''}`}
        onClick=${() => setPreset(p.id)}>${p.name}<small>${p.desc}</small></button>`)}</div>
      <div class="filters">
        <label class="fld">產業<select value=${industry} onChange=${(e) => setIndustry(e.target.value)}>
          <option value="">全部產業</option>${(res?.industries || []).map((x) => html`<option value=${x}>${x}</option>`)}</select></label>
        <label class="fld">市場<select value=${market} onChange=${(e) => setMarket(e.target.value)}>
          <option value="">上市＋上櫃</option><option value="TWSE">上市</option><option value="TPEX">上櫃</option></select></label>
        <label class="fld">月均量下限（張）<input type="number" step="100" min="0" value=${minVol} onChange=${(e) => setMinVol(parseFloat(e.target.value) || 0)} /></label>
        <div class="mut covline">${cov ? html`法人 <span class="num">${cov.inst_date || '—'}</span> · 融資 <span class="num">${cov.margin_date || '—'}</span>
          · 本益比 <span class="num">${cov.valuation_date || '—'}</span> · 營收 <span class="num">${cov.revenue_ym || '—'}</span>` : ''}</div>
      </div>
    </section>
    ${err && html`<div class="toast">${err}</div>`}
    ${cov && !cov.with_chips && html`<div class="panel"><${NoChips} /></div>`}
    <section class="panel" style="min-width:0">
      <div class="ph">${pr?.name || '法人選股'}${industry ? ` · ${industry}` : ''}
        <span class="sub">${busy ? '計算中… · ' : ''}${res ? `${res.total} 檔 · 點欄位排序 · ` : ''}
          ${rows.length ? html`<a href="javascript:void 0" onClick=${() => alphaCsv(rows)}>匯出 CSV</a>` : ''}</span></div>
      <div class="mut" style="font-size:12px;padding:10px 16px 0;line-height:1.7">
        總分 = 籌碼 35% ＋ 動能 25% ＋ 成長 25% ＋ 估值 15%。每個指標先在全市場排百分位（0～100），大型股、小型股可以直接比較。
        四因子小圖由左到右：籌碼、動能、成長、估值。</div>
      ${res ? html`<${AlphaTable} rows=${rows} watch=${watch} source="法人選股" date=${health?.db?.latest_date} sort=${sort} onSort=${(k) => setSort(sort?.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: -1 })} />` : html`<div class="empty">計算中…</div>`}
    </section>`;
}

// ---------------------------------------------------------------- 產業輪動
function Sectors({ health }) {
  const [list, setList] = useState(null);
  const [mode, setMode] = useState('chg');
  useEffect(() => { if (health?.db?.latest_date) api('/api/alpha/sectors').then(setList); }, [health?.db?.latest_date]);
  if (health && !health.db.latest_date) return html`<${EmptyDb} />`;
  const val = (x) => (mode === 'chg' ? x.chg : mode === 'legal5' ? x.legal5 : x.score - 50);
  const scale = Math.max(0.1, ...(list || []).map((x) => Math.abs(val(x))));
  const bg = (v) => {
    const a = Math.min(1, Math.abs(v) / scale) * 0.75 + 0.08;
    return v > 0 ? `rgba(255,90,95,${a})` : v < 0 ? `rgba(43,196,138,${a})` : '#1A2131';
  };
  return html`<section class="panel">
    <div class="ph">產業輪動熱力圖<span class="sub">方塊依成交金額排序，點一下看該產業的法人選股</span></div>
    <div class="pb" style="display:flex;flex-direction:column;gap:14px">
      <div class="seg">${[['chg', '今日漲跌'], ['legal5', '法人 5 日淨買超'], ['score', '平均法人評分']].map(([k, l]) => html`
        <button type="button" class=${mode === k ? 'on' : ''} onClick=${() => setMode(k)}>${l}</button>`)}</div>
      ${!list ? html`<div class="empty">計算中…</div>` : html`<div class="heat">${list.map((x) => html`
        <a class="tile" href=${`#/alpha/all/${encodeURIComponent(x.industry)}`} style=${`background:${bg(val(x))}`}>
          <b>${x.industry}</b>
          <span class="num big">${mode === 'chg' ? signed(x.chg, 2, '%') : mode === 'legal5' ? `${yi(x.legal5)} 億` : fmt(x.score, 0)}</span>
          <span class="num small">${x.up}/${x.n} 上漲 · 成交 ${fmt(x.turnover, 0)} 億</span>
        </a>`)}</div>`}
    </div>
  </section>`;
}

// ---------------------------------------------------------------- 雷達
const COLS = [
  ['code', '代號', 'l'], ['name', '名稱', 'l'], ['entry', '進場分數'], ['stage', '位階'], ['price', '現價'], ['chg', '漲跌%'],
  ['bias', '季線乖離%'], ['ret20', '20日漲幅%'], ['alpha', '法人評分'], ['amp', '振幅%'],
  ['vol', '今日量(張)'], ['vr', '量比'], ['power', '多空力道'], ['gap', '跳空%'], ['bw', '布林寬%'],
  ['ma_diff', '均線糾結%'], ['ma60', '季線'],
];
const ENTRY_PARTS = [['pos', '位置', 30], ['brk', '突破', 25], ['base', '盤整', 15], ['chip', '法人', 30]];
const LIGHT = { green: '🟢', yellow: '🟡', red: '🔴' };
const STAGE_CLS = { 起漲: 'up', 中段: 'hot', 高檔: 'dn', 季線下: '' };

function EntryScore({ r }) {
  if (r.entry == null) return html`<span class="mut">—</span>`;
  return html`<span class=${`entry ${r.light}`} title=${(r.reasons || []).join('\n')}>
    ${LIGHT[r.light]} <b class="num">${r.entry}</b></span>
    <div class="eparts num">${ENTRY_PARTS.map(([k, l]) => `${l}${Math.round(r.entry_parts?.[k] ?? 0)}`).join(' ')}</div>`;
}

function EntryCard({ m }) {
  if (!m || m.entry == null) return null;
  return html`<div class="panel">
    <div class="ph">進場分數<span class="sub">剛起漲還是已經漲一波</span></div>
    <div class="pb" style="display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;align-items:baseline;gap:10px">
        <span class=${`entry ${m.light}`} style="font-size:28px">${LIGHT[m.light]} <b class="num">${m.entry}</b></span>
        <span class="mut">/ 100</span><span class=${`tag ${STAGE_CLS[m.stage] || ''}`}>${m.stage}</span></div>
      ${ENTRY_PARTS.map(([k, l, max], i) => html`<div>
        <div class="frow"><span class="mut">${l}</span><span class="meter"><span style=${`width:${(m.entry_parts[k] / max) * 100}%;background:${m.entry_parts[k] / max >= 0.7 ? C.up : m.entry_parts[k] / max >= 0.5 ? C.accent : '#3A4458'}`}></span></span><span class="num">${fmt(m.entry_parts[k], 0)}/${max}</span></div>
        <div class="mut" style="font-size:12px;margin:2px 0 0 44px">${m.reasons[i]}</div></div>`)}
      <div class="mut" style="font-size:12px">70 分以上 🟢、50～69 🟡、未滿 50 🔴。規則化參考，不是投資建議。</div>
    </div>
  </div>`;
}

function useWatched() {
  const [items, setItems] = useState([]);
  const load = () => api('/api/watch').then((r) => setItems(r.rows)).catch(() => {});
  useEffect(() => { load(); }, []);
  const add = (code, source) => post('/api/watch', { code, source }).then(load);
  const has = (code, date, source) => items.some((w) => w.code === code && w.added_date === date && (!source || w.source === source));
  return { items, add, has };
}

function WatchBtn({ code, date, source, watch }) {
  const [busy, setBusy] = useState(false);
  const done = watch.has(code, date, source);
  const go = (e) => {
    e.stopPropagation();
    if (done || busy) return;
    setBusy(true);
    watch.add(code, source).catch((er) => alert(er.message)).finally(() => setBusy(false));
  };
  return html`<button type="button" class=${`wbtn ${done ? 'on' : ''}`} onClick=${go} title=${done ? '已加入觀察名單' : '記下今天的價格與進場分數，之後自動追蹤'}>
    ${done ? '✓ 觀察中' : busy ? '…' : '＋ 觀察'}</button>`;
}

function HitTable({ rows, compact, sort, onSort, watch, source, date }) {
  if (!rows.length) return html`<div class="empty">沒有符合條件的股票。寧可空手，絕不追高。</div>`;
  const cols = compact ? COLS.filter(([k]) => ['code', 'name', 'entry', 'stage', 'price', 'chg', 'vol', 'vr'].includes(k)) : COLS;
  return html`<div class="tablewrap"><table>
    <thead><tr>
      ${cols.map(([k, label, al]) => html`<th class=${`${al || ''} ${onSort ? 'sortable' : ''} ${sort?.key === k ? 'sorted' : ''}`}
        onClick=${() => onSort && onSort(k)}>${label}${sort?.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>${k === 'name' && watch ? html`<th class="l">觀察</th>` : ''}`)}
      ${!compact && html`<th>20 日走勢</th>`}<th class="l">訊號</th>
    </tr></thead>
    <tbody>${rows.map((r) => html`<tr onClick=${() => { location.hash = `#/stock/${r.code}`; }}>
      <td class="l num acc">${r.code}</td><td class="l">${r.name}</td>
      ${watch && html`<td class="l"><${WatchBtn} code=${r.code} date=${date} source=${source} watch=${watch} /></td>`}
      <td><${EntryScore} r=${r} /></td><td><span class=${`tag ${STAGE_CLS[r.stage] || ''}`}>${r.stage}</span></td>
      <td class="num">${fmt(r.price)}</td><td class=${`num ${cls(r.chg)}`}>${signed(r.chg, 2, '%')}</td>
      ${!compact && html`<td class=${`num ${cls(r.bias)}`}>${signed(r.bias, 1, '%')}</td><td class=${`num ${cls(r.ret20)}`}>${signed(r.ret20, 1, '%')}</td>
        <td class="num">${r.alpha == null ? '—' : r.alpha}</td><td class="num">${fmt(r.amp)}</td>`}
      <td class="num">${fmt(r.vol, 0)}</td><td class="num acc">${fmt(r.vr, 1)}x</td>
      ${!compact && html`
        <td class=${`num ${cls(r.power)}`}>${signed(r.power, 1)}</td><td class="num">${fmt(r.gap)}</td>
        <td class="num">${fmt(r.bw)}</td><td class="num">${fmt(r.ma_diff)}</td><td class="num">${fmt(r.ma60)}</td>
        <td><${Spark} data=${r.spark} /></td>`}
      <td class="l">${r.growth && html`<span class="tag hot">成長K線</span>`}${r.washout && html`<span class="tag up">洗盤換手</span>`}
        ${r.gap >= 2 && html`<span class="tag">跳空</span>`}${r.bw <= 15 && html`<span class="tag">布林壓縮</span>`}</td>
    </tr>`)}</tbody></table></div>`;
}

function toCsv(rows) {
  const cell = (v) => (typeof v === 'string' && /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : (v ?? ''));
  const head = [...COLS.map(([, l]) => l), '燈號', '判讀理由'].join(',');
  const body = rows.map((r) => [...COLS.map(([k]) => cell(r[k])), LIGHT[r.light] || '',
    cell((r.reasons || []).join('；'))].join(',')).join('\n');
  const blob = new Blob(['﻿' + head + '\n' + body], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `雷達掃描_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

function Radar({ initialTactic, health }) {
  const [tactics, setTactics] = useState([]);
  const [tactic, setTactic] = useState(initialTactic || 'dive');
  const [params, setParams] = useState(null);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sort, setSort] = useState({ key: 'entry', dir: -1 });
  const watch = useWatched();

  const run = (body) => {
    setBusy(true); setErr('');
    post('/api/scan', body).then((r) => { setRes(r); setParams(r.params); })
      .catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  useEffect(() => { api('/api/tactics').then(setTactics); }, []);
  useEffect(() => { if (health?.db?.latest_date) run({ tactic }); }, [health?.db?.latest_date]);

  const pick = (id) => { setTactic(id); run({ tactic: id }); };
  const setP = (k, v) => setParams({ ...params, [k]: v });
  const sorted = useMemo(() => {
    if (!res) return [];
    const { key, dir } = sort;
    return [...res.rows].sort((a, b) => {
      const x = a[key], y = b[key];
      return (typeof x === 'string' ? x.localeCompare(y) : (x ?? -1e18) - (y ?? -1e18)) * dir;
    });
  }, [res, sort]);
  const onSort = (k) => setSort(sort.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: -1 });

  if (health && !health.db.latest_date) return html`<${EmptyDb} />`;
  const num = (k, label, step) => html`<label class="fld">${label}
    <input type="number" step=${step} value=${params?.[k] ?? ''} onInput=${(e) => setP(k, parseFloat(e.target.value) || 0)} /></label>`;
  const chk = (k, label) => html`<label class="chk"><input type="checkbox" checked=${!!params?.[k]}
    onChange=${(e) => setP(k, e.target.checked)} />${label}</label>`;

  return html`<div class="row">
    <aside class="panel" style="flex:1 1 280px;max-width:330px">
      <div class="ph">掃描條件<span class="sub">選戰術後可微調</span></div>
      <div class="pb" style="display:flex;flex-direction:column;gap:16px">
        <div class="pills">${tactics.map((t) => html`<button type="button" class=${`pill ${t.id === tactic ? 'on' : ''}`}
          onClick=${() => pick(t.id)}>${t.name}<small>${t.desc}</small></button>`)}</div>
        <div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
          ${num('p', '股價上限（元）', 1)}${num('v', '月均量上限（張）', 10)}
          ${num('tv', '今日成交下限（張）', 50)}${num('pm', '量能爆發（倍）', 0.1)}
        </div>
        <div>${chk('sqz', '布林帶寬 ≤ 15%')}${chk('ma', '月季線糾結 ≤ 3%')}${chk('gap', '跳空開高 ≥ 2%')}${chk('wash', '極端洗盤換手')}</div>
        <button type="button" class="btn" disabled=${busy} onClick=${() => run({ tactic, params })}>${busy ? '掃描中…' : '掃描全市場'}</button>
        ${res && html`<div class="mut" style="font-size:12px;line-height:1.7">資料日期 <span class="num">${res.latest}</span>，
          計算 <span class="num">${res.universe.toLocaleString()}</span> 檔，耗時 <span class="num">${res.elapsed}</span> 秒；
          略過 <span class="num">${res.skipped}</span> 檔（最新一日無成交或不足 60 天）</div>`}
      </div>
    </aside>
    <section class="panel" style="flex:999 1 560px;min-width:0">
      <div class="ph">掃描結果 · ${tactics.find((t) => t.id === tactic)?.name || '自訂'}
        <span class="sub">${res ? `${res.rows.length} 檔符合 · 點欄位排序 · ` : ''}
          ${res?.rows.length ? html`<a href="javascript:void 0" onClick=${() => toCsv(sorted)}>匯出 CSV</a>` : ''}</span></div>
      ${err && html`<div class="empty up">${err}</div>`}
      ${res ? html`<${HitTable} rows=${sorted} sort=${sort} onSort=${onSort} watch=${watch}
        source=${tactics.find((t) => t.id === tactic)?.name || '自訂掃描'} date=${res.latest} />` : html`<div class="empty">計算中…</div>`}
    </section>
  </div>`;
}

// ---------------------------------------------------------------- 個股
function KChart({ data }) {
  const el = useRef(null);
  useEffect(() => {
    if (!el.current || !data) return undefined;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: C.muted, fontFamily: 'Consolas, monospace' },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.line }, timeScale: { borderColor: C.line },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: 'zh-TW', dateFormat: 'yyyy-MM-dd' },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false,
    });
    candle.setData(data.candles);
    candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } });
    const line = (pts, color, width = 1.5) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(pts);
    };
    line(data.ma5, '#C9D1E0', 1); line(data.ma20, C.accent); line(data.ma60, C.blue);
    line(data.bb_upper, '#3A4458', 1); line(data.bb_lower, '#3A4458', 1);
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: 'vol', priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false });
    vol.setData(data.candles.map((c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? 'rgba(255,90,95,.45)' : 'rgba(43,196,138,.45)' })));
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data]);
  return html`<div class="chart" ref=${el}></div>`;
}

function StockSearch({ stocks, current }) {
  const [q, setQ] = useState('');
  const go = (e) => {
    e.preventDefault();
    const code = (q.match(/\d{4}/) || [])[0] || stocks.find((s) => s.name === q.trim())?.code;
    if (code) { location.hash = `#/stock/${code}`; setQ(''); }
  };
  return html`<form class="search" onSubmit=${go}>
    <label class="mut" for="stock-q">搜尋股票</label>
    <input id="stock-q" list="stock-list" value=${q} placeholder=${current || '代號或名稱，例如 2330'} onInput=${(e) => setQ(e.target.value)} />
    <datalist id="stock-list">${stocks.map((s) => html`<option value=${`${s.code} ${s.name}`} />`)}</datalist>
    <button class="btn ghost" type="submit">查詢</button>
  </form>`;
}

function Defense({ code, price, health }) {
  const [cost, setCost] = useState('');
  const [shares, setShares] = useState(1000);
  const [disc, setDisc] = useState(1);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { setRes(null); setCost(price ? String(price) : ''); }, [code]);
  const run = (e) => {
    e.preventDefault(); setErr('');
    api(`/api/stock/${code}/defense?cost=${cost}&shares=${shares}&fee_discount=${disc}`).then(setRes).catch((x) => setErr(x.message));
  };
  const v = res?.verdict;
  return html`<div class="panel">
    <div class="ph">持倉防禦約談<span class="sub">含手續費與證交稅</span></div>
    <form class="pb" onSubmit=${run} style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;align-items:end">
      <label class="fld">買進均價<input type="number" step="0.01" value=${cost} onInput=${(e) => setCost(e.target.value)} /></label>
      <label class="fld">股數<input type="number" step="1" value=${shares} onInput=${(e) => setShares(e.target.value)} /></label>
      <label class="fld">手續費折數<input type="number" step="0.05" min="0.1" max="1" value=${disc} onInput=${(e) => setDisc(e.target.value)} /></label>
      <button class="btn" type="submit">約談</button>
    </form>
    ${err && html`<div class="empty up">${err}</div>`}
    ${res && html`<div style="padding:0 16px 16px">
      <div class="kv"><span class="mut">總成本（含手續費）</span><span class="num">${fmt(res.total_cost, 0)}</span></div>
      <div class="kv"><span class="mut">賣出淨值（扣稅費）</span><span class="num">${fmt(res.net_value, 0)}</span></div>
      <div class="kv"><span class="mut">未實現損益</span><span class=${`num ${cls(res.unrealized)}`}>${signed(res.unrealized, 0)} · ${signed(res.roi_pct, 2, '%')}</span></div>
      <div class="kv"><span class="mut">開盤直接跌停</span><span class="num">${fmt(res.limit_down_price)} → <span class=${cls(res.limit_down_pl)}>${signed(res.limit_down_pl, 0)}</span></span></div>
      <div class="kv"><span class="mut">季線防線</span><span class="num">${fmt(res.ma60)} · 乖離 ${signed(res.bias_ma60_pct, 1, '%')}</span></div>
      ${res.escape_weak && html`<div class="kv"><span class="mut">反彈逃命點</span><span class="num">10日線 ${fmt(res.escape_weak)} · 20日線 ${fmt(res.escape_strong)}</span></div>`}
      <div class=${`verdict ${v.level}`} style="margin-top:12px"><h3>裁決：${v.title}</h3>${v.text}</div>
    </div>`}
  </div>`;
}

function ChipChart({ inst, margin }) {
  const W = 900, H = 170, pad = 4;
  const days = inst.slice(-20);
  if (!days.length) return html`<div class="empty">還沒有這檔的法人資料。</div>`;
  const max = Math.max(1, ...days.flatMap((d) => [Math.abs(d.foreign), Math.abs(d.trust)]));
  const bw = (W - pad * 2) / days.length;
  const mid = H / 2;
  const y = (v) => (v / max) * (mid - 10);
  const mm = margin.filter((m) => days.some((d) => d.date === m.date));
  const mlo = Math.min(...mm.map((m) => m.margin_bal)), mhi = Math.max(...mm.map((m) => m.margin_bal));
  const mx = (date) => pad + days.findIndex((d) => d.date === date) * bw + bw / 2;
  const my = (v) => 12 + (1 - (v - mlo) / ((mhi - mlo) || 1)) * (H - 24);
  return html`<svg viewBox="0 0 ${W} ${H + 18}" class="chipsvg" role="img" aria-label="近 20 日外資、投信買賣超與融資餘額">
    <line x1="0" x2=${W} y1=${mid} y2=${mid} stroke=${C.line} />
    ${days.map((d, i) => {
      const x = pad + i * bw;
      return html`<g><title>${d.date}　外資 ${signed(d.foreign, 0)} 張　投信 ${signed(d.trust, 0)} 張　自營 ${signed(d.dealer, 0)} 張</title>
        <rect x=${x + bw * 0.12} width=${bw * 0.36} y=${d.foreign > 0 ? mid - y(d.foreign) : mid} height=${Math.abs(y(d.foreign))} fill=${C.blue} />
        <rect x=${x + bw * 0.52} width=${bw * 0.36} y=${d.trust > 0 ? mid - y(d.trust) : mid} height=${Math.abs(y(d.trust))} fill=${C.accent} />
        ${(i % 5 === 0 || i === days.length - 1) && html`<text x=${x + bw / 2} y=${H + 14} fill=${C.muted} font-size="11" text-anchor="middle">${d.date.slice(5)}</text>`}</g>`;
    })}
    ${mm.length > 1 && html`<polyline fill="none" stroke="#C9D1E0" stroke-width="1.5" stroke-dasharray="4 3"
      points=${mm.map((m) => `${mx(m.date).toFixed(1)},${my(m.margin_bal).toFixed(1)}`).join(' ')} />`}
  </svg>`;
}

function ScoreCard({ chips }) {
  const r = chips?.score;
  if (!chips) return null;
  if (!r || !chips.coverage.with_chips) return html`<div class="panel"><div class="ph">法人評分卡</div><${NoChips} /></div>`;
  const kv = (label, v) => html`<div class="kv"><span class="mut">${label}</span><span class="num">${v}</span></div>`;
  const streak = (n) => (n == null ? '—' : n > 0 ? html`<span class="up">連買 ${n} 日</span>` : n < 0 ? html`<span class="dn">連賣 ${-n} 日</span>` : '—');
  const sg = (v, d = 0, u = '') => html`<span class=${cls(v)}>${signed(v, d, u)}</span>`;
  return html`<div class="panel">
    <div class="ph">法人評分卡<span class="sub">全市場第 ${r.rank} / ${chips.universe} 名</span></div>
    <div class="pb" style="display:flex;flex-direction:column;gap:10px">
      <${Score} v=${r.score} big />
      ${[['籌碼', r.s_chip], ['動能', r.s_momentum], ['成長', r.s_growth], ['估值', r.s_value]].map(([n, v]) => html`
        <div class="frow"><span class="mut">${n}</span><span class="meter"><span style=${`width:${v}%;background:${v >= 70 ? C.up : v >= 50 ? C.accent : '#3A4458'}`}></span></span><span class="num">${fmt(v, 0)}</span></div>`)}
      <div>${(r.tags || []).map((t) => html`<span class=${`tag ${tagClass(t)}`}>${t}</span>`)}</div>
    </div>
    <div style="padding:0 16px 12px">
      ${kv('外資', html`${streak(r.f_streak)} · 5日 ${sg(r.f5)} · 20日 ${sg(r.f20)}`)}
      ${kv('投信', html`${streak(r.t_streak)} · 5日 ${sg(r.t5)} · 20日 ${sg(r.t20)}`)}
      ${kv('法人 5 日買超佔成交量', sg(r.legal5_ratio, 1, '%'))}
      ${kv('融資餘額 · 5 日變化', html`${fmt(r.margin_bal, 0)} 張 · ${sg(r.margin_chg5)}`)}
      ${kv('券資比', r.short_ratio == null ? '—' : `${fmt(r.short_ratio, 1)}%`)}
      ${kv('本益比 · 淨值比 · 殖利率', `${fmt(r.pe, 1)} · ${fmt(r.pb)} · ${fmt(r.dy)}%`)}
      ${kv(`月營收 ${r.rev_ym || ''}`, html`年增 ${sg(r.yoy, 1, '%')} · 月增 ${sg(r.mom, 1, '%')}`)}
      ${kv('累計營收年增', sg(r.cum_yoy, 1, '%'))}
      ${kv('20 日 · 60 日漲幅', html`${sg(r.ret20, 1, '%')} · ${sg(r.ret60, 1, '%')}`)}
    </div>
  </div>`;
}

const TF = [['D', '日K', '日'], ['W', '週K', '週'], ['M', '月K', '月']];

function Stock({ code, stocks, health }) {
  const [data, setData] = useState(null);
  const [chart, setChart] = useState(null);
  const [tf, setTf] = useState('D');
  const [chips, setChips] = useState(null);
  const [err, setErr] = useState('');
  const watch = useWatched();
  useEffect(() => {
    if (!code) return;
    setData(null); setChart(null); setChips(null); setErr(''); setTf('D');
    api(`/api/stock/${code}`).then((d) => { setData(d); setChart(d); }).catch((e) => setErr(e.message));
    api(`/api/stock/${code}/chips`).then(setChips).catch(() => {});
  }, [code]);
  const pickTf = (t) => {
    setTf(t);
    if (t === 'D') { setChart(data); return; }
    api(`/api/stock/${code}?tf=${t}`).then(setChart).catch((e) => setErr(e.message));
  };
  const unit = TF.find(([k]) => k === tf)[2];
  const bars = chart?.candles.length || 0;
  const m = data?.metrics;
  const last = data?.candles[data.candles.length - 1];
  const prev = data?.candles[data.candles.length - 2];
  const chg = last && prev ? last.close - prev.close : null;
  return html`
    <${StockSearch} stocks=${stocks} current=${code} />
    ${!code && html`<div class="panel"><div class="empty">輸入股票代號或名稱開始分析，或從 <a href="#/radar">雷達掃描</a> 點選股票。</div></div>`}
    ${err && html`<div class="panel"><div class="empty up">${err}</div></div>`}
    ${data && html`
      <section class="hdr">
        <div><div class="mut" style="font-size:13px">${data.code} · ${data.market === 'TPEX' ? '上櫃' : '上市'}</div>
          <div style="font-size:26px;font-weight:700">${data.name}</div></div>
        <div class=${`px ${cls(chg)}`}>${fmt(last.close)}</div>
        <div class=${`num ${cls(chg)}`} style="font-size:16px">${signed(chg)} ${signed(chg / prev.close * 100, 2, '%')}</div>
        <div class="ticker">
          <span><span class="mut">開</span> <span class="num">${fmt(last.open)}</span></span>
          <span><span class="mut">高</span> <span class="num">${fmt(last.high)}</span></span>
          <span><span class="mut">低</span> <span class="num">${fmt(last.low)}</span></span>
          <span><span class="mut">量</span> <span class="num">${fmt(last.volume, 0)} 張</span></span>
        </div>
        ${m && html`<${WatchBtn} code=${data.code} date=${health?.db?.latest_date} source="個股分析" watch=${watch} />`}
      </section>
      <div class="row">
        <div class="panel" style="flex:999 1 560px">
          <div class="ph">K 線圖
            <span class="seg">${TF.map(([k, l]) => html`<button type="button" class=${tf === k ? 'on' : ''} onClick=${() => pickTf(k)}>${l}</button>`)}</span>
            <span class="sub">${bars} 根${unit} K · 資料庫保留 ${data.history_days || data.candles.length} 個交易日</span></div>
          <div class="legend"><span style="color:#C9D1E0">— 5${unit}線</span><span class="acc">— 20${unit}線</span><span style="color:var(--blue)">— 60${unit}線</span><span class="mut">— 布林通道</span></div>
          ${tf !== 'D' && bars < 20 && html`<div class="mut" style="font-size:12px;padding:0 16px">目前資料只有 ${data.history_days} 個交易日，${unit} K 根數還少；更新資料庫會自動回補到約 1 年。</div>`}
          <div style="padding:8px 8px 12px"><${KChart} data=${chart || data} /></div>
          <div class="ph" style="border-top:1px solid var(--line)">法人籌碼 · 近 20 日<span class="sub">
            <span style="color:var(--blue)">■ 外資</span>　<span class="acc">■ 投信</span>　<span style="color:#C9D1E0">┅ 融資餘額</span>　單位：張</span></div>
          <div style="padding:8px 12px 12px">${chips ? html`<${ChipChart} inst=${chips.inst} margin=${chips.margin} />` : html`<div class="empty">載入中…</div>`}</div>
        </div>
        <div style="flex:1 1 340px;display:flex;flex-direction:column;gap:16px;min-width:0">
          <${EntryCard} m=${m} />
          <${ScoreCard} chips=${chips} />
          <div class="panel">
            <div class="ph">雷達指標<span class="sub">${data.candles.length < 60 ? '不足 60 日，部分指標無法計算' : '最新交易日'}</span></div>
            ${m ? html`<div style="padding:4px 16px 12px">
              <div class="kv"><span class="mut">月量爆發倍數</span><span class="num acc">${fmt(m.vr, 1)}x</span></div>
              <div class="kv"><span class="mut">多空力道</span><span class=${`num ${cls(m.power)}`}>${signed(m.power, 1)}</span></div>
              <div class="kv"><span class="mut">布林帶寬</span><span class="num">${fmt(m.bw)}%</span></div>
              <div class="kv"><span class="mut">月季線糾結</span><span class="num">${fmt(m.ma_diff)}%</span></div>
              <div class="kv"><span class="mut">跳空缺口</span><span class="num">${fmt(m.gap)}%</span></div>
              <div class="kv"><span class="mut">季線</span><span class="num">${fmt(m.ma60)}</span></div>
              <div class="kv"><span class="mut">訊號</span><span>${m.growth ? html`<span class="tag hot">成長K線</span>` : ''}${m.washout ? html`<span class="tag up">洗盤換手</span>` : ''}${!m.growth && !m.washout ? html`<span class="mut">無</span>` : ''}</span></div>
            </div>` : html`<div class="empty">最新一日沒有成交或資料不足。</div>`}
          </div>
          <${Defense} code=${code} price=${last?.close} health=${health} />
        </div>
      </div>`}`;
}

// ---------------------------------------------------------------- 投信預測
function HitBars({ days }) {
  const max = 100;
  return html`<div class="hitbars">${days.map((d) => html`<div class="hb" title=${`${d.date} 預測 → ${d.next}：命中 ${d.hits}/${d.n}（${d.hit}%），全市場 ${d.base}%`}>
    <span class="bar" style=${`height:${(d.hit / max) * 100}%`}></span><span class="base" style=${`bottom:${(d.base / max) * 100}%`}></span></div>`)}</div>
    <div class="mut" style="display:flex;justify-content:space-between;font-size:11px;margin-top:4px"><span>${days[0]?.next}</span><span>${days[days.length - 1]?.next}</span></div>`;
}

function TrustPage({ health }) {
  const [r, setR] = useState(null);
  const [err, setErr] = useState('');
  const watch = useWatched();
  useEffect(() => { setR(null); api('/api/trust/predict').then(setR).catch((e) => setErr(e.message)); }, [health?.db?.latest_date]);
  if (err) return html`<div class="panel"><div class="empty up">${err}</div></div>`;
  if (!r) return html`<div class="panel"><div class="empty">計算中…（第一次約需 10 秒）</div></div>`;
  if (!r.ready) return html`<div class="panel"><div class="empty">法人資料只有 ${r.inst_days} 天，至少要 ${r.need} 天才能算機率。
    請到 <a href="#/settings">資料與設定</a> 按「更新資料庫」，程式會自動回補約 120 天的法人資料。</div></div>`;
  const bt = r.backtest;
  return html`
    <section class="grid g-kpi">
      <div class="panel pb kpi"><div class="label">資料日期</div><div class="value">${r.asof}</div><div class="subv mut">預測下一個交易日投信動向</div></div>
      <div class="panel pb kpi"><div class="label">前 20 名命中率（近 ${bt.days.length} 天回測）</div>
        <div class="value up">${fmt(bt.avg_hit, 1)}%</div><div class="subv mut">隔天投信真的買超的比例</div></div>
      <div class="panel pb kpi"><div class="label">隨便挑一檔的機率</div><div class="value">${fmt(bt.avg_base, 1)}%</div>
        <div class="subv mut">全市場平均每天被投信買超的比例</div></div>
      <div class="panel pb kpi"><div class="label">預測效果</div><div class="value acc">${fmt(bt.lift, 1)} 倍</div>
        <div class="subv mut">命中率是隨便挑的幾倍</div></div>
    </section>
    <section class="panel">
      <div class="ph">每日命中率<span class="sub">橘色 = 前 20 名命中率 · 灰線 = 全市場平均 · 每天只用當天以前的資料預測，不偷看答案</span></div>
      <div class="pb"><${HitBars} days=${bt.days} /></div>
    </section>
    <section class="panel">
      <div class="ph">明日投信續買機率排行<span class="sub">依 ${r.asof} 收盤後資料 · 20 日均量 300 張以上 · 機率是歷史統計，不是保證</span></div>
      <div class="tablewrap"><table>
        <thead><tr><th class="l">代號</th><th class="l">名稱</th><th class="l">觀察</th><th>續買機率</th><th>投信連買</th><th>今日投信(張)</th><th>5日投信(張)</th><th>買超佔量</th><th>月線</th><th>收盤</th><th class="l">判斷依據</th></tr></thead>
        <tbody>${r.rows.map((x) => html`<tr onClick=${() => { location.hash = `#/stock/${x.code}`; }}>
          <td class="l num acc">${x.code}</td><td class="l">${x.name}</td>
          <td class="l"><${WatchBtn} code=${x.code} date=${r.asof} source="投信預測" watch=${watch} /></td>
          <td class="num"><b class=${x.prob >= 60 ? 'up' : x.prob >= 40 ? 'acc' : ''}>${fmt(x.prob, 0)}%</b></td>
          <td class=${`num ${cls(x.streak)}`}>${x.streak > 0 ? `${x.streak} 天` : x.streak < 0 ? `賣 ${-x.streak} 天` : '—'}</td>
          <td class=${`num ${cls(x.t1)}`}>${signed(x.t1, 0)}</td><td class=${`num ${cls(x.t5)}`}>${signed(x.t5, 0)}</td>
          <td class="num">${fmt(x.intensity, 1)}%</td><td>${x.trend === 'up' ? html`<span class="tag up">站上</span>` : html`<span class="tag">跌破</span>`}</td>
          <td class="num">${fmt(x.close)}</td><td class="l mut" style="font-size:12px;min-width:320px">${x.why}</td></tr>`)}</tbody>
      </table></div>
    </section>`;
}

// ---------------------------------------------------------------- 觀察名單
const HZ = [['d3', '3 天'], ['d5', '1 週'], ['d10', '2 週']];

function WatchStats({ title, sub, list }) {
  if (!list?.length) return null;
  const cell = (s) => (s.n ? html`<span class=${`num ${cls(s.avg)}`}>${signed(s.avg, 1, '%')}</span>
    <div class="eparts num">勝率 ${s.win}% · 贏0050 ${s.beat ?? '—'}% · ${s.n} 筆</div>` : html`<span class="mut">尚無資料</span>`);
  return html`<section class="panel">
    <div class="ph">${title}<span class="sub">${sub}</span></div>
    <div class="tablewrap"><table>
      <thead><tr><th class="l">${title === '各戰術準確度' ? '戰術 / 來源' : '進場分數'}</th><th>筆數</th>
        ${HZ.map(([, l]) => html`<th>${l}後平均</th>`)}<th>至今平均</th></tr></thead>
      <tbody>${list.map((s) => html`<tr class=${s.name === '全部' ? 'tot' : ''}>
        <td class="l">${s.name}</td><td class="num">${s.n}</td>
        ${HZ.map(([k]) => html`<td>${cell(s[k])}</td>`)}
        <td class=${`num ${cls(s.ret_avg)}`}>${signed(s.ret_avg, 1, '%')}</td></tr>`)}</tbody>
    </table></div>
  </section>`;
}

function WatchPage({ health }) {
  const [rep, setRep] = useState(null);
  const [err, setErr] = useState('');
  const load = () => api('/api/watch').then(setRep).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [health?.db?.latest_date]);
  const del = (w) => { if (confirm(`把 ${w.code} ${w.name}（${w.added_date} 加入）從觀察名單移除？`)) api(`/api/watch/${w.id}`, { method: 'DELETE' }).then(load); };
  const pct = (v, bench) => (v == null ? html`<span class="mut">—</span>` : html`<span class=${`num ${cls(v)}`}>${signed(v, 1, '%')}</span>
    ${bench != null && html`<div class="eparts num">0050 ${signed(bench, 1, '%')}</div>`}`);
  if (err) return html`<div class="panel"><div class="empty up">${err}</div></div>`;
  if (!rep) return html`<div class="panel"><div class="empty">讀取中…</div></div>`;
  return html`
    <section class="panel">
      <div class="ph">觀察名單<span class="sub">以加入當天收盤價為基準 · 3 天／1 週／2 週 = 之後第 3／5／10 個交易日收盤</span></div>
      ${!rep.rows.length ? html`<div class="empty">還沒有觀察的股票。到 <a href="#/radar">雷達掃描</a> 或個股分析按「＋ 觀察」，就會記下當天的價格和進場分數。</div>` : html`
      <div class="tablewrap"><table>
        <thead><tr><th class="l">代號</th><th class="l">名稱</th><th class="l">來源</th><th class="l">加入日</th><th>加入價</th><th>進場分數</th>
          ${HZ.map(([, l]) => html`<th>${l}後</th>`)}<th>至今</th><th>期間最高</th><th>期間最低</th><th class="l">狀態</th><th></th></tr></thead>
        <tbody>${rep.rows.map((w) => html`<tr onClick=${() => { location.hash = `#/stock/${w.code}`; }}>
          <td class="l num acc">${w.code}</td><td class="l">${w.name}</td><td class="l mut">${w.source}</td>
          <td class="l num">${w.added_date}</td><td class="num">${fmt(w.price)}</td>
          <td><${EntryScore} r=${{ ...w, entry_parts: w.parts }} /></td>
          ${HZ.map(([k]) => html`<td>${pct(w[k], w[`${k}_bench`])}</td>`)}
          <td>${pct(w.ret, w.bench_ret)}<div class="eparts num">${w.last != null ? `現價 ${fmt(w.last)}` : ''}</div></td>
          <td class=${`num ${cls(w.max_up)}`}>${w.max_up == null ? '—' : signed(w.max_up, 1, '%')}</td>
          <td class=${`num ${cls(w.max_dn)}`}>${w.max_dn == null ? '—' : signed(w.max_dn, 1, '%')}</td>
          <td class="l mut" style="white-space:nowrap">${w.days >= 10 ? '已滿 2 週' : `第 ${w.days} 個交易日`}</td>
          <td><button type="button" class="wbtn" onClick=${(e) => { e.stopPropagation(); del(w); }}>移除</button></td>
        </tr>`)}</tbody></table></div>`}
    </section>
    <${WatchStats} title="各戰術準確度" sub="勝率 = 上漲的比例 · 贏0050 = 漲得比同期 0050 多的比例" list=${rep.stats} />
    <${WatchStats} title="進場分數準確度" sub="分數越高的，之後是不是真的漲得比較多" list=${rep.buckets} />`;
}

// ---------------------------------------------------------------- 持倉（第二批）
const LV = { danger: '危險', take: '獲利了結', warn: '注意', ok: '安全' };

function useYuantaStatus(poll = 0) {
  const [st, setSt] = useState(null);
  const load = () => api('/api/yuanta/status').then(setSt).catch(() => {});
  useEffect(() => {
    load();
    if (!poll) return undefined;
    const t = setInterval(load, poll);
    return () => clearInterval(t);
  }, []);
  return [st, load, setSt];
}

function usePositions(enabled, every = 5000) {
  const [pos, setPos] = useState(null);
  const [err, setErr] = useState('');
  const load = (refresh) => api(`/api/positions${refresh ? '?refresh=true' : ''}`)
    .then((r) => { setPos(r); setErr(''); }).catch((e) => setErr(e.message));
  useEffect(() => {
    if (!enabled) return undefined;
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, every);
    return () => clearInterval(t);
  }, [enabled]);
  return [pos, err, load];
}

function AlertList({ alerts, limit }) {
  if (!alerts?.length) return html`<div class="empty">目前沒有警示，持股都在防線內。</div>`;
  return html`<div class="alerts">${alerts.slice(0, limit || 99).map((a) => html`
    <a class=${`alert lv-${a.level}`} href=${`#/stock/${a.code}`}>
      <b>${a.title}</b><span>${a.text}</span></a>`)}</div>`;
}

function PositionAlerts() {
  const [st] = useYuantaStatus(15000);
  const [pos] = usePositions(!!st?.logged_in, 15000);
  if (!st) return html`<div class="empty">讀取中…</div>`;
  if (!st.logged_in) {
    return html`<div class="empty">${st.configured ? html`元大還沒連線。到 <a href="#/positions">持倉風控</a> 會自動連線，之後跌破季線、重挫、虧損擴大都會在這裡跳出。`
      : '要先在 config.local.toml 設定元大帳號。'}</div>`;
  }
  if (!pos) return html`<div class="empty">讀取庫存中…</div>`;
  const t = pos.totals;
  return html`<div class="pb" style="display:flex;flex-direction:column;gap:10px">
    <div class="kv"><span class="mut">持股 ${t.count} 檔 · 市值</span><span class="num">${won(t.value)}</span></div>
    <div class="kv"><span class="mut">今日損益</span><span class=${`num ${cls(t.day_pnl)}`}>${signed(t.day_pnl, 0)}（${signed(t.day_pct, 2, '%')}）</span></div>
    <div class="kv"><span class="mut">未實現損益</span><span class=${`num ${cls(t.pnl)}`}>${signed(t.pnl, 0)}（${signed(t.pnl_pct, 2, '%')}）</span></div>
    <${AlertList} alerts=${pos.alerts} limit=${3} />
  </div>`;
}

function YuantaPanel({ st, onConnect, onDisconnect, busy }) {
  return html`<div class="panel"><div class="ph">元大帳務連線<span class="sub">唯讀：只查庫存、報價、損益，程式沒有任何下單功能</span></div>
    <div class="pb ybar">
      ${st?.logged_in ? html`<span class="chip ok">● 已連線</span>` : st?.connecting || busy ? html`<span class="chip">連線中…（約 5～10 秒）</span>`
        : html`<span class="chip off">未連線</span>`}
      <span class="mut">${st?.environment || ''} ${st?.account || ''}${st?.connected_at ? ` · ${st.connected_at} 登入` : ''}</span>
      <span style="margin-left:auto;display:flex;gap:8px">
        ${st?.logged_in ? html`<button class="btn ghost" onClick=${onDisconnect}>登出</button>`
          : html`<button class="btn" disabled=${busy || !st?.configured} onClick=${onConnect}>連線元大</button>`}
      </span>
    </div>
    ${st?.error && !st.logged_in && html`<div class="pb" style="padding-top:0"><div class="verdict danger"><h3>連線失敗</h3><pre class="errpre">${st.error}</pre></div></div>`}
    ${st && !st.configured && html`<div class="empty">還沒設定元大帳號：請在 config.local.toml 的 [yuanta] 填好帳號、電子密碼與元件資料夾（和「測試元大連線.bat」用的是同一份設定）。</div>`}
  </div>`;
}

function Positions() {
  const [st, loadSt, setSt] = useYuantaStatus();
  const [busy, setBusy] = useState(false);
  const tried = useRef(false);
  const connect = async () => {
    setBusy(true);
    try { setSt(await post('/api/yuanta/connect')); } catch (e) { await loadSt(); }
    setBusy(false);
    dispatchEvent(new Event('yuanta'));
  };
  const disconnect = async () => { setSt(await post('/api/yuanta/disconnect')); dispatchEvent(new Event('yuanta')); };
  useEffect(() => {   // 打開這頁就自動連線一次（失敗就不再自動重試，避免用掉登入次數）
    if (st && st.configured && !st.logged_in && !st.connecting && !st.error && !tried.current) {
      tried.current = true; connect();
    }
  }, [st]);
  const [pos, err, loadPos] = usePositions(!!st?.logged_in);
  const [days, setDays] = useState(30);
  const [rz, setRz] = useState(null);
  useEffect(() => {
    if (!st?.logged_in) return;
    setRz(null);
    api(`/api/positions/realized?days=${days}`).then(setRz).catch((e) => setRz({ error: e.message }));
  }, [st?.logged_in, days]);
  const t = pos?.totals;
  const go = (code) => { location.hash = `#/stock/${code}`; };
  return html`
    <${YuantaPanel} st=${st} busy=${busy} onConnect=${connect} onDisconnect=${disconnect} />
    ${st?.logged_in && html`
    ${err && html`<div class="toast">${err}</div>`}
    <section class="grid g-kpi">
      <div class="panel pb kpi"><div class="label">股票市值</div><div class="value">${t ? won(t.value) : '—'}</div>
        <div class="subv mut">${t ? `${t.count} 檔 · 成本 ${won(t.cost)}` : '讀取中…'}</div></div>
      <div class="panel pb kpi"><div class="label">今日損益</div><div class=${`value ${cls(t?.day_pnl)}`}>${t ? signed(t.day_pnl, 0) : '—'}</div>
        <div class=${`subv ${cls(t?.day_pct)}`}>${t ? signed(t.day_pct, 2, '%') : ''}</div></div>
      <div class="panel pb kpi"><div class="label">未實現損益</div><div class=${`value ${cls(t?.pnl)}`}>${t ? signed(t.pnl, 0) : '—'}</div>
        <div class=${`subv ${cls(t?.pnl_pct)}`}>${t ? signed(t.pnl_pct, 2, '%') : ''}</div></div>
      <div class="panel pb kpi"><div class="label">警示</div><div class=${`value ${pos?.alerts?.length ? 'acc' : ''}`}>${pos ? pos.alerts.length : '—'} <span class="mut" style="font-size:16px">則</span></div>
        <div class="subv mut">${pos ? `報價 ${pos.now} · 庫存 ${pos.holdings_time}` : ''}</div></div>
    </section>
    <section class="grid g-2">
      <div class="panel" style="grid-column:1/-1">
        <div class="ph">我的庫存<span class="sub">即時價每 5 秒更新 · 損益已估算賣出手續費與證交稅 ·
          <button class="linkbtn" onClick=${() => loadPos(true)}>重新查詢庫存</button></span></div>
        ${!pos ? html`<div class="empty">讀取庫存中…</div>` : !pos.rows.length ? html`<div class="empty">目前沒有股票庫存。</div>` : html`
        <div class="tablewrap"><table>
          <thead><tr><th class="l">股票</th><th>股數</th><th>均價</th><th>現價</th><th>今日</th><th>市值</th><th>未實現損益</th>
            <th>季線</th><th>距季線</th><th class="l">防禦裁決</th></tr></thead>
          <tbody>${pos.rows.map((r) => html`<tr onClick=${() => go(r.code)}>
            <td class="l"><span class="num acc">${r.code}</span> ${r.name}${r.kind !== '現股' ? html` <span class="tag">${r.kind}</span>` : ''}</td>
            <td class="num">${r.shares.toLocaleString()}</td>
            <td class="num">${fmt(r.avg, 2)}</td>
            <td class="num">${fmt(r.price, 2)}${r.live ? html`<i class="live" title="元大即時報價"></i>` : ''}</td>
            <td class=${`num ${cls(r.day_chg_pct)}`}>${signed(r.day_chg_pct, 2, '%')}<br /><small>${signed(r.day_pnl, 0)}</small></td>
            <td class="num">${won(r.value)}</td>
            <td class=${`num ${cls(r.pnl)}`}>${signed(r.pnl, 0)}<br /><small>${signed(r.pnl_pct, 2, '%')}</small></td>
            <td class="num mut">${fmt(r.ma60, 2)}</td>
            <td class=${`num ${cls(r.dist_ma60)}`}>${signed(r.dist_ma60, 1, '%')}</td>
            <td class="l">${r.verdict ? html`<span class=${`tag lvtag lv-${r.verdict.level}`}>${LV[r.verdict.level]}</span> ${r.verdict.title}` : html`<span class="mut">資料不足</span>`}</td>
          </tr>`)}</tbody>
        </table></div>`}
      </div>
    </section>
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">即時警示<span class="sub">跌破季線、逼近季線、今日重挫、虧損超過 8%、過熱</span></div>
        ${pos ? html`<div class="pb"><${AlertList} alerts=${pos.alerts} /></div>` : html`<div class="empty">讀取中…</div>`}
      </div>
      <div class="panel">
        <div class="ph">已實現損益<span class="sub"><span class="seg">${[7, 30, 90, 365].map((d) => html`
          <button class=${days === d ? 'on' : ''} onClick=${() => setDays(d)}>${d === 365 ? '1 年' : `${d} 天`}</button>`)}</span></span></div>
        ${!rz ? html`<div class="empty">查詢中…</div>` : rz.error ? html`<div class="empty">${rz.error}</div>` : !rz.count ? html`<div class="empty">這段期間沒有已實現損益。</div>` : html`
          <div class="pb" style="padding-bottom:0"><div class="kv"><span class="mut">合計（${rz.count} 筆，賺錢 ${rz.wins} 筆）</span>
            <span class=${`num ${cls(rz.total)}`} style="font-size:18px">${signed(rz.total, 0)}</span></div></div>
          <div class="tablewrap"><table>
            <thead><tr><th class="l">日期</th><th class="l">股票</th><th>股數</th><th>成交價</th><th>損益</th></tr></thead>
            <tbody>${rz.rows.map((r) => html`<tr onClick=${() => go(r.code)}>
              <td class="l num mut">${r.date}</td><td class="l num acc">${r.code}</td><td class="num">${(r.shares || 0).toLocaleString()}</td>
              <td class="num">${fmt(r.price, 2)}</td><td class=${`num ${cls(r.pnl)}`}>${signed(r.pnl, 0)}</td></tr>`)}</tbody>
          </table></div>`}
      </div>
    </section>`}
    ${st?.messages?.length ? html`<details class="panel msgs"><summary class="ph">元大連線訊息<span class="sub">診斷用</span></summary>
      <pre class="errpre pb">${st.messages.join('\n')}</pre></details>` : ''}`;
}

// ---------------------------------------------------------------- 資料與設定
function Settings({ health, reload }) {
  const [job, setJob] = useState(health?.update || {});
  useEffect(() => {
    if (!job.running) return undefined;
    const t = setInterval(() => api('/api/db/update').then((j) => { setJob(j); if (!j.running) reload(); }), 1500);
    return () => clearInterval(t);
  }, [job.running]);
  const db = health?.db;
  const s = health?.settings;
  return html`<div class="grid g-2">
    <div class="panel">
      <div class="ph">本地資料庫<span class="sub">證交所、櫃買中心官方盤後資料</span></div>
      <div style="padding:4px 16px 16px">
        <div class="kv"><span class="mut">最新資料日期</span><span class="num">${db?.latest_date || '尚無資料'}</span></div>
        <div class="kv"><span class="mut">交易日數</span><span class="num">${db?.trading_days ?? '—'}</span></div>
        <div class="kv"><span class="mut">股票數 / 最新一日有資料</span><span class="num">${db?.stocks ?? '—'} / ${db?.stocks_on_latest ?? '—'}</span></div>
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:10px">
          <button class="btn" type="button" disabled=${job.running} onClick=${() => post('/api/db/update').then(setJob)}>
            ${job.running ? '更新中…' : '更新資料庫'}</button>
          ${job.running && html`<div class="progress"><div style=${`width:${(job.progress || 0) * 100}%`}></div></div>`}
          ${job.message && html`<div class="mut" style="font-size:12px">${job.running ? job.message : (job.summary || '')}</div>`}
          ${job.errors?.length ? html`<div class="up" style="font-size:12px">${job.errors.slice(0, 5).map((e) => html`<div>${e}</div>`)}</div>` : ''}
          <div class="mut" style="font-size:12px;line-height:1.7">建議每個交易日 15:30 後更新一次（三大法人、融資融券約 15:00～16:00 公布）。第一次建庫約 10～15 分鐘，之後約半分鐘。</div>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="ph">個人設定<span class="sub">config.local.toml</span></div>
      <div style="padding:4px 16px 16px">
        <div class="kv"><span class="mut">資料庫保留交易日</span><span class="num">${s?.history_trading_days ?? '—'}</span></div>
        <div class="mut" style="font-size:12px;margin-top:12px;line-height:1.7">要修改請用記事本編輯專案資料夾的 config.local.toml，存檔後重新啟動戰情室。</div>
      </div>
    </div>
    ${db?.recent_errors?.length ? html`<div class="panel" style="grid-column:1/-1">
      <div class="ph">最近的下載錯誤</div>
      <div class="tablewrap"><table><thead><tr><th class="l">市場</th><th class="l">日期</th><th class="l">訊息</th></tr></thead>
      <tbody>${db.recent_errors.map((e) => html`<tr><td class="l">${e.market}</td><td class="l num">${e.date}</td><td class="l">${e.message}</td></tr>`)}</tbody></table></div>
    </div>` : ''}
  </div>`;
}

// ---------------------------------------------------------------- 主程式
function App() {
  const [page, arg, arg2] = useHash();
  const [health, setHealth] = useState(null);
  const [breadth, setBreadth] = useState(null);
  const [stocks, setStocks] = useState([]);
  const [err, setErr] = useState('');
  const load = () => {
    api('/api/health').then(setHealth).catch((e) => setErr(`連不到戰情室後端：${e.message}`));
    api('/api/market/breadth').then(setBreadth).catch(() => {});
    api('/api/stocks').then(setStocks).catch(() => {});
  };
  useEffect(load, []);
  useEffect(() => {   // 元大連線時，大盤數字每分鐘更新（盤中即時）
    const loadB = () => { if (!document.hidden) api('/api/market/breadth').then(setBreadth).catch(() => {}); };
    const t = setInterval(loadB, 30000);
    addEventListener('yuanta', loadB);
    return () => { clearInterval(t); removeEventListener('yuanta', loadB); };
  }, []);
  let body;
  switch (page) {
    case 'arena': body = html`<${ArenaPage} health=${health} />`; break;
    case 'alpha': body = html`<${Alpha} preset=${arg} industry=${arg2} health=${health} />`; break;
    case 'sectors': body = html`<${Sectors} health=${health} />`; break;
    case 'radar': body = html`<${Radar} initialTactic=${arg} health=${health} />`; break;
    case 'stock': body = html`<${Stock} code=${arg} stocks=${stocks} health=${health} />`; break;
    case 'watch': body = html`<${WatchPage} health=${health} />`; break;
    case 'trust': body = html`<${TrustPage} health=${health} />`; break;
    case 'positions': body = html`<${Positions} />`; break;
    case 'settings': body = html`<${Settings} health=${health} reload=${load} />`; break;
    default: body = html`<${Overview} health=${health} breadth=${breadth} />`;
  }
  return html`<div class="app">
    <${TopBar} health=${health} breadth=${breadth} />
    <div class="body">
      <${SideNav} page=${page || 'overview'} health=${health} />
      <main class="main">${body}</main>
    </div>
    ${err && html`<div class="toast">${err}</div>`}
  </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
