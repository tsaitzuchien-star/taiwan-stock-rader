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
  ['overview', '總覽'], ['alpha', '法人選股'], ['sectors', '產業輪動'], ['radar', '雷達掃描'], ['stock', '個股分析'],
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
        <span><span class="mut">資料日期</span> <b class="num">${breadth.latest}</b></span>
        <span><span class="mut">上漲</span> <b class="num up">${breadth.up}</b></span>
        <span><span class="mut">下跌</span> <b class="num dn">${breadth.down}</b></span>
        <span><span class="mut">站上季線</span> <b class="num">${fmt(breadth.above_ma60_pct, 1)}%</b></span>`}
    </div>
    <div class="right">
      <span class="chip off" title="第二批會接上元大即時報價">元大 未連線</span>
      <span class="chip num">${now.toLocaleTimeString('zh-TW', { hour12: false })}</span>
      ${db && html`<span class="mut">${db.stocks?.toLocaleString()} 檔 · ${db.trading_days} 日</span>`}
    </div>
  </header>`;
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
      <div class="panel pb kpi"><div class="label">資料日期</div><div class="value">${breadth?.latest || '—'}</div>
        <div class="subv mut">官方盤後資料</div></div>
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
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">法人綜合評分 前 10 名<span class="sub"><a href="#/alpha">看全部</a></span></div>
        ${alpha && !alpha.coverage.with_chips ? html`<${NoChips} />` : html`<${AlphaTable} rows=${(alpha?.rows || []).slice(0, 10)} compact />`}
      </div>
      <${FlowsPanel} flows=${flows} />
    </section>
    <section class="grid g-2">
      <div class="panel">
        <div class="ph">雷達今日命中 · 深海潛艦<span class="sub">${hits ? `${hits.rows.length} 檔` : '計算中…'} · <a href="#/radar">看全部</a></span></div>
        <${HitTable} rows=${(hits?.rows || []).slice(0, 10)} compact />
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
        <div class="ph">即時警示<span class="sub">持倉＋自選</span></div>
        <div class="empty">接上元大即時報價後，跌破季線、爆量等事件會在這裡即時跳出（第二批）。</div>
      </div>
    </section>`;
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

function AlphaTable({ rows, compact, sort, onSort }) {
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
      onClick=${() => onSort && !nosort && onSort(k)}>${label}${sort?.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`)}
      ${!compact && html`<th class="l">訊號</th>`}</tr></thead>
    <tbody>${rows.map((r) => html`<tr onClick=${() => { location.hash = `#/stock/${r.code}`; }}>
      ${cols.map(([k]) => cell(r, k))}
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
      ${res ? html`<${AlphaTable} rows=${rows} sort=${sort} onSort=${(k) => setSort(sort?.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: -1 })} />` : html`<div class="empty">計算中…</div>`}
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
  ['code', '代號', 'l'], ['name', '名稱', 'l'], ['price', '現價'], ['chg', '漲跌%'], ['amp', '振幅%'],
  ['vol', '今日量(張)'], ['vr', '量比'], ['power', '多空力道'], ['gap', '跳空%'], ['bw', '布林寬%'],
  ['ma_diff', '均線糾結%'], ['ma60', '季線'],
];

function HitTable({ rows, compact, sort, onSort }) {
  if (!rows.length) return html`<div class="empty">沒有符合條件的股票。寧可空手，絕不追高。</div>`;
  const cols = compact ? COLS.filter(([k]) => ['code', 'name', 'price', 'chg', 'vol', 'vr'].includes(k)) : COLS;
  return html`<div class="tablewrap"><table>
    <thead><tr>
      ${cols.map(([k, label, al]) => html`<th class=${`${al || ''} ${onSort ? 'sortable' : ''} ${sort?.key === k ? 'sorted' : ''}`}
        onClick=${() => onSort && onSort(k)}>${label}${sort?.key === k ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`)}
      ${!compact && html`<th>20 日走勢</th>`}<th class="l">訊號</th>
    </tr></thead>
    <tbody>${rows.map((r) => html`<tr onClick=${() => { location.hash = `#/stock/${r.code}`; }}>
      <td class="l num acc">${r.code}</td><td class="l">${r.name}</td>
      <td class="num">${fmt(r.price)}</td><td class=${`num ${cls(r.chg)}`}>${signed(r.chg, 2, '%')}</td>
      ${!compact && html`<td class="num">${fmt(r.amp)}</td>`}
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
  const head = COLS.map(([, l]) => l).join(',');
  const body = rows.map((r) => COLS.map(([k]) => (r[k] ?? '')).join(',')).join('\n');
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
  const [sort, setSort] = useState({ key: 'vr', dir: -1 });

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
      ${res ? html`<${HitTable} rows=${sorted} sort=${sort} onSort=${onSort} />` : html`<div class="empty">計算中…</div>`}
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

function Stock({ code, stocks, health }) {
  const [data, setData] = useState(null);
  const [chips, setChips] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (!code) return;
    setData(null); setChips(null); setErr('');
    api(`/api/stock/${code}`).then(setData).catch((e) => setErr(e.message));
    api(`/api/stock/${code}/chips`).then(setChips).catch(() => {});
  }, [code]);
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
      </section>
      <div class="row">
        <div class="panel" style="flex:999 1 560px">
          <div class="ph">日 K 線<span class="sub">資料庫保留 ${data.candles.length} 個交易日</span></div>
          <div class="legend"><span style="color:#C9D1E0">— MA5</span><span class="acc">— MA20</span><span style="color:var(--blue)">— MA60</span><span class="mut">— 布林通道</span></div>
          <div style="padding:8px 8px 12px"><${KChart} data=${data} /></div>
          <div class="ph" style="border-top:1px solid var(--line)">法人籌碼 · 近 20 日<span class="sub">
            <span style="color:var(--blue)">■ 外資</span>　<span class="acc">■ 投信</span>　<span style="color:#C9D1E0">┅ 融資餘額</span>　單位：張</span></div>
          <div style="padding:8px 12px 12px">${chips ? html`<${ChipChart} inst=${chips.inst} margin=${chips.margin} />` : html`<div class="empty">載入中…</div>`}</div>
        </div>
        <div style="flex:1 1 340px;display:flex;flex-direction:column;gap:16px;min-width:0">
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

// ---------------------------------------------------------------- 持倉（第二批）
function Positions() {
  return html`<div class="panel"><div class="ph">持倉風控<span class="sub">元大帳務</span></div>
    <div class="empty">這頁會接上元大：庫存、即時損益、季線防線、與已實現損益。<br />
    元大正式環境測試通過後就會推出。現在可以先到 <a href="#/stock">個股分析</a> 用「持倉防禦約談」手動試算。</div></div>`;
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
  let body;
  switch (page) {
    case 'alpha': body = html`<${Alpha} preset=${arg} industry=${arg2} health=${health} />`; break;
    case 'sectors': body = html`<${Sectors} health=${health} />`; break;
    case 'radar': body = html`<${Radar} initialTactic=${arg} health=${health} />`; break;
    case 'stock': body = html`<${Stock} code=${arg} stocks=${stocks} health=${health} />`; break;
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
