"""第四艙：元大帳務與即時報價（Streamlit 介面）。只查詢，不下單。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from .config import Settings
from .indicators import merge_intraday
from .yuanta import YuantaClient, YuantaConfig, YuantaError
from .yuanta.parsers import snapshot_bars

CONFIG_SAMPLE = """[yuanta]
account = "S0000000000"      # S + 分公司 4 碼 + 帳號 7 碼
password = "你的電子密碼"
environment = "PROD"         # 正式環境；想先用元大測試帳號就改 "UAT"
sdk_dir = 'C:\\Users\\User\\TaiwanStockRadar'   # YuantaSparkAPI.dll 所在資料夾
"""


@st.cache_resource(show_spinner=False)
def get_client(_settings: Settings) -> YuantaClient:
    """整個程式只建立一個元大連線（重新整理網頁不會重覆登入）。"""
    return YuantaClient(YuantaConfig.from_dict(_settings.yuanta))


def _mask(acct: str) -> str:
    return acct[:5] + "*" * max(len(acct) - 5, 0) if acct else "(未設定)"


def _market(code: str, twse_set: set[str]) -> str:
    return "TWSE" if code in twse_set else "TPEX"


def intraday_bars(client: YuantaClient, codes: list[str], bars: pd.DataFrame,
                  twse_set: set[str]) -> tuple[pd.DataFrame, str]:
    """盤中模式：向元大查詢這批股票的即時報價，做成今天的 K 棒接到歷史資料後面。"""
    today = date.today()
    if today.weekday() >= 5:
        return bars, "今天不是交易日，使用資料庫盤後資料。"
    latest = bars['date'].max() if not bars.empty else None
    if latest is not None and latest.date() >= today:
        return bars, "資料庫已有今天的盤後資料，直接使用。"
    pairs = [(c, _market(c, twse_set)) for c in codes]
    bar = st.progress(0.0, text="⚡ 向元大查詢即時報價…")
    snap = client.snapshot(pairs, progress=lambda f: bar.progress(f, text=f"⚡ 向元大查詢即時報價… {f:.0%}"))
    bar.empty()
    today_bars = snapshot_bars(snap.values(), today)
    return merge_intraday(bars, today_bars), f"⚡ 盤中模式：已用元大即時報價補上今天 {len(today_bars)} 檔的 K 棒。"


def _send_to_tab3(code: str, price: float, qty: int) -> None:
    st.session_state["emp_code_tab3"] = code
    st.session_state["emp_cost_tab3"] = float(round(price, 2)) if price else 0.1
    st.session_state["emp_shares_tab3"] = int(qty) or 1


def render(settings: Settings, names: dict[str, str], twse_set: set[str]) -> None:
    client = get_client(settings)
    cfg = client.config
    env_label = "正式環境" if cfg.environment.upper() != "UAT" else "測試環境"

    st.markdown("<div class='control-panel'>", unsafe_allow_html=True)
    st.markdown("<h3>💼 第四艙：元大帳務與即時報價</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color:#8b92a5;'>透過元大 Spark API 讀取你的庫存、損益與即時報價。此功能只查詢，不會下單。</p>",
                unsafe_allow_html=True)

    problems = cfg.problems()
    if problems:
        st.warning("元大連線還沒設定好：\n\n- " + "\n- ".join(problems))
        st.markdown("請用記事本打開專案資料夾裡的 `config.local.toml`（沒有就把 `config.example.toml` 複製一份改名），"
                    "加入下面這段後存檔，再重新整理網頁。這個檔案只存在你的電腦，不會上傳。")
        st.code(CONFIG_SAMPLE, language="toml")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    status = "🟢 已登入" if client.logged_in else "⚪ 未連線"
    c1.markdown(f"**{status}**　{env_label}　帳號 {_mask(cfg.account)}")
    if not client.logged_in:
        if c2.button("🔌 連線登入元大", use_container_width=True, key="yt_connect"):
            with st.spinner("連線元大中…（約 5～10 秒）"):
                try:
                    client.connect()
                    st.session_state.pop("yt_holdings", None)
                    st.rerun()
                except YuantaError as e:
                    st.error(f"❌ {e}")
    else:
        if c3.button("⏏️ 中斷連線", use_container_width=True, key="yt_close"):
            client.close()
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    if not client.logged_in:
        _render_messages(client)
        return

    _render_holdings(client, names)
    _render_quotes(client, names, twse_set)
    _render_realized(client)
    _render_messages(client)


def _render_holdings(client: YuantaClient, names: dict[str, str]) -> None:
    st.markdown("#### 📦 我的庫存")
    if st.button("🔄 重新查詢庫存", key="yt_refresh_holdings") or "yt_holdings" not in st.session_state:
        try:
            st.session_state["yt_holdings"] = client.holdings()
            st.session_state["yt_holdings_at"] = datetime.now().strftime("%H:%M:%S")
        except YuantaError as e:
            st.error(f"❌ 查詢庫存失敗：{e}")
            return
    df: pd.DataFrame = st.session_state["yt_holdings"]
    if df.empty:
        st.info("目前沒有股票庫存。")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("持股檔數", f"{df['股票代號'].nunique()} 檔")
    m2.metric("股票市值", f"NT$ {df['股票市值'].sum():,.0f}")
    total_pl, total_cost = df['未實現損益'].sum(), df['持有成本'].sum()
    m3.metric("未實現損益", f"NT$ {total_pl:,.0f}",
              f"{total_pl / total_cost * 100:.2f}%" if total_cost else None)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(f"查詢時間 {st.session_state.get('yt_holdings_at', '')}（資料來源：元大 GetStoreSummary）")

    # 庫存自動訂閱即時報價
    pairs = list(zip(df['股票代號'], df['市場'].replace('', 'TWSE')))
    try:
        new = [p for p in pairs if p[0] not in client.subscribed]
        if new:
            client.snapshot(new)
            client.subscribe(new)
    except YuantaError as e:
        st.warning(f"庫存即時報價訂閱失敗：{e}")

    options = {f"{r['股票代號']} {r['股票名稱']}（{r['股數']:,} 股）": r for _, r in df.iterrows()}
    pick_col, btn_col = st.columns([3, 1])
    pick = pick_col.selectbox("選一檔庫存送到第三艙做防禦約談：", list(options), key="yt_pick")
    if pick:
        r = options[pick]
        btn_col.markdown("<br>", unsafe_allow_html=True)
        btn_col.button("🚨 帶入第三艙", use_container_width=True, key="yt_to_tab3",
                       on_click=_send_to_tab3, args=(r['股票代號'], r['成交均價'] or 0, r['股數']))


def _render_quotes(client: YuantaClient, names: dict[str, str], twse_set: set[str]) -> None:
    st.markdown("#### 📈 即時報價")
    col_in, col_btn = st.columns([3, 1])
    extra = col_in.text_input("加入其他股票（用空白或逗號分隔）", placeholder="例如：2330 2317 6488", key="yt_extra")
    col_btn.markdown("<br>", unsafe_allow_html=True)
    if col_btn.button("➕ 訂閱", use_container_width=True, key="yt_sub") and extra:
        codes = [c for c in extra.replace(",", " ").replace("，", " ").split() if c]
        unknown = [c for c in codes if c not in names]
        known = [c for c in codes if c in names]
        if unknown:
            st.warning(f"查無代號：{'、'.join(unknown)}")
        pairs = [(c, _market(c, twse_set)) for c in known]
        try:
            client.snapshot(pairs)
            client.subscribe(pairs)
        except YuantaError as e:
            st.error(f"❌ {e}")
    st.caption(f"已訂閱 {len(client.subscribed)} 檔（元大上限 2000 檔，本程式最多 1900 檔）")

    @st.fragment(run_every=3)
    def live_table():
        df = client.quote_table(client.subscribed, names)
        if df.empty:
            st.info("尚未訂閱任何股票。")
            return
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"每 3 秒自動更新　{datetime.now():%H:%M:%S}")

    live_table()


def _render_realized(client: YuantaClient) -> None:
    with st.expander("💰 已實現損益查詢"):
        c1, c2, c3 = st.columns([1, 1, 1])
        start = c1.date_input("開始日", date.today() - timedelta(days=30), key="yt_rs")
        end = c2.date_input("結束日", date.today(), key="yt_re")
        c3.markdown("<br>", unsafe_allow_html=True)
        if c3.button("查詢", use_container_width=True, key="yt_realized"):
            try:
                df = client.realized(start, end)
            except YuantaError as e:
                st.error(f"❌ {e}")
                return
            if df.empty:
                st.info("這段期間沒有已實現損益。")
            else:
                st.metric("合計已實現損益", f"NT$ {df['已實現損益'].sum():,.0f}")
                st.dataframe(df, use_container_width=True, hide_index=True)


def _render_messages(client: YuantaClient) -> None:
    if client.messages:
        with st.expander("🛰️ 元大連線訊息"):
            st.text("\n".join(list(client.messages)[-30:]))
