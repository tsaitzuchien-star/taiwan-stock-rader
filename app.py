import streamlit as st
import pandas as pd
import requests
import time
import random
import json
import os
from datetime import datetime
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from bs4 import BeautifulSoup

# ==========================================
# 1. 頁面與環境設定
# ==========================================
st.set_page_config(page_title="台股妖股雷達 V10.0 | 企業帝國版", layout="wide", page_icon="🏢")

# ==========================================
# 2. 戰情日誌與狀態記憶系統 (Session State)
# ==========================================
LOG_FILE = "scan_log.json"
TODAY_STR = datetime.now().strftime("%Y/%m/%d")

def load_scan_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if v.startswith(TODAY_STR)}
        except: return {}
    return {}

def save_scan_log(log_data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=4)

scan_log = load_scan_log()

# 定義四大戰術參數
TACTICS = {
    "🛠️ 自訂義模式": None,
    "🌊 戰術一：深海潛艦 (經典起漲)": {"p": 50.0, "v": 1000, "tv": 500, "pm": 4.0, "sqz": True, "ma": True, "gap": False, "wash": False},
    "🌋 戰術二：大怒神 (極端洗盤)": {"p": 80.0, "v": 2000, "tv": 1500, "pm": 2.5, "sqz": False, "ma": False, "gap": False, "wash": True},
    "⚡ 戰術三：閃電戰 (跳空突破)": {"p": 50.0, "v": 1000, "tv": 500, "pm": 2.5, "sqz": True, "ma": False, "gap": True, "wash": False},
    "🐂 戰術四：老牛翻身 (穩健推升)": {"p": 100.0, "v": 3000, "tv": 1000, "pm": 2.0, "sqz": False, "ma": True, "gap": False, "wash": False}
}

# 初始化 UI 狀態
for key, default in [("p_limit", 50.0), ("v_limit", 2000), ("tv_limit", 1000), ("pm_limit", 1.5),
                     ("sqz_chk", True), ("ma_chk", True), ("gap_chk", False), ("wash_chk", False)]:
    if key not in st.session_state:
        st.session_state[key] = default

def apply_tactic():
    selected = st.session_state.tactic_selector
    if TACTICS[selected]:
        cfg = TACTICS[selected]
        st.session_state.p_limit = cfg["p"]
        st.session_state.v_limit = cfg["v"]
        st.session_state.tv_limit = cfg["tv"]
        st.session_state.pm_limit = cfg["pm"]
        st.session_state.sqz_chk = cfg["sqz"]
        st.session_state.ma_chk = cfg["ma"]
        st.session_state.gap_chk = cfg["gap"]
        st.session_state.wash_chk = cfg["wash"]

# ==========================================
# 3. 企業級視覺 CSS
# ==========================================
st.markdown("""
    <style>
    .stApp { background-color: #171b26; }
    h1, h2, h3, h4, p, span, div { color: #d1d4dc; font-family: 'Segoe UI', Tahoma, sans-serif; }
    div[data-testid="metric-container"] { background-color: #1e222d; border: 1px solid #2b313f; border-radius: 12px; padding: 20px; border-left: 4px solid #FF00FF; }
    div[data-testid="metric-container"] label { color: #8b92a5 !important; font-size: 1.1rem !important; font-weight: bold;}
    header {visibility: hidden;}
    .control-panel { background-color: #1e222d; border: 1px solid #2b313f; border-radius: 12px; padding: 25px; margin-bottom: 20px; }
    .risk-panel { background-color: #131722; border: 1px solid #FFD700; border-radius: 12px; padding: 20px; margin-bottom: 20px; border-left: 6px solid #FFD700; }
    .shoeshine-panel { background-color: #2b1111; border: 1px solid #ff4b4b; border-radius: 12px; padding: 20px; margin-top: 10px; margin-bottom: 20px; border-left: 6px solid #ff4b4b; }
    .interview-panel { background-color: #1e293b; border: 1px solid #3b82f6; border-radius: 12px; padding: 25px; margin-top: 20px; margin-bottom: 20px; border-left: 6px solid #3b82f6; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #1e293b; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #8b92a5; border: 1px solid #2b313f; border-bottom: none; }
    .stTabs [aria-selected="true"] { background-color: #FF00FF !important; color: white !important; font-weight: bold; }
    .stSelectbox label, .stTextInput label, .stRadio label, .stSlider label { color: #3b82f6 !important; font-weight: bold; font-size: 1.1rem !important; }
    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div { background-color: #1e293b !important; border: 1px solid #3b82f6 !important; }
    div.stButton > button { background-color: #1e293b !important; color: #FF00FF !important; border: 1px solid #FF00FF !important; font-weight: bold !important; font-size: 1.1rem !important; transition: all 0.3s ease; }
    div.stButton > button:hover { background-color: #FF00FF !important; color: #ffffff !important; box-shadow: 0 0 10px rgba(255, 0, 255, 0.5) !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 4. 戰略底層：政府直連 (上市 + 上櫃)
# ==========================================
@st.cache_data(ttl=43200, show_spinner=False)
def get_real_time_stock_list():
    stock_dict = {}
    twse_set = set()
    try:
        res_twse = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10)
        if res_twse.status_code == 200:
            for item in res_twse.json():
                code, name = str(item.get('Code', '')), str(item.get('Name', ''))
                if len(code) == 4 and code.isdigit():
                    stock_dict[code] = name
                    twse_set.add(code)
    except: pass
    try:
        res_tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        if res_tpex.status_code == 200:
            for item in res_tpex.json():
                code, name = str(item.get('SecuritiesCompanyCode', '')), str(item.get('CompanyName', ''))
                if len(code) == 4 and code.isdigit():
                    stock_dict[code] = name
    except: pass
    if not stock_dict:
        return ['2330', '2317', '2454', '2603'], {'2330':'台積電', '2317':'鴻海', '2454':'聯發科', '2603':'長榮'}, set(['2330', '2317', '2454', '2603'])
    return sorted(list(stock_dict.keys())), stock_dict, twse_set

pure_stocks, stock_names_dict, twse_set = get_real_time_stock_list()

BATCH_SIZE = 140
batch_options = ["🌟 全市場總掃描 (約需 3~5 分鐘，建議使用)"]
if pure_stocks:
    for i in range(0, len(pure_stocks), BATCH_SIZE):
        batch_options.append(f"第 {i//BATCH_SIZE + 1} 部隊 (排序 {i+1}~{min(i + BATCH_SIZE, len(pure_stocks))})")
    batch_options.append("隨機游擊隊 (全市場隨機抽 140 檔)")

# ==========================================
# 5. 國庫風控面板 
# ==========================================
st.markdown("<h1>🏢 台股妖股雷達 <span style='color: #FFD700;'>V10.0</span> <span style='font-size: 0.5em; color: #8b92a5;'>(企業帝國版)</span></h1>", unsafe_allow_html=True)
st.markdown("<div class='risk-panel'>", unsafe_allow_html=True)
st.markdown("<h3>🏛️ 秉宸好帥 - 國庫資金防護網</h3>", unsafe_allow_html=True)
rc1, rc2, rc3 = st.columns(3)
TOTAL_CAPITAL = 1170000
MAX_RISK_PCT = 0.05 
MAX_EXPOSURE = TOTAL_CAPITAL * MAX_RISK_PCT
rc1.metric("🛡️ 大本營總戰備資金", f"NT$ {TOTAL_CAPITAL:,}")
rc2.metric("⚠️ 單檔極限曝險 (5%)", f"NT$ {int(MAX_EXPOSURE):,}")
rc3.metric("🚦 系統狀態", "V10.0 企業模組上線", delta="面試審核系統啟動", delta_color="normal")
st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 6. 情報工具箱 (快取函式與雙重流動性濾網)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def get_ptt_shoeshine_index(stock_name):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    url = f"https://www.ptt.cc/bbs/Stock/search?q={stock_name}"
    try:
        res = requests.get(url, headers=headers, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        return len(soup.find_all('div', class_='title'))
    except: return -1

@st.cache_data(ttl=3600, show_spinner=False)
def get_kline_data(stock_id, is_otc):
    yf_ticker = f"{stock_id}.TWO" if is_otc else f"{stock_id}.TW"
    try:
        return yf.Ticker(yf_ticker).history(period="6mo")
    except: return pd.DataFrame()

def add_liquidity_filter(df, volume_col='Volume', threshold=500):
    df = df.sort_index(ascending=True) 
    df['MA5_Volume'] = (df[volume_col] / 1000).rolling(window=5).mean()
    df['MA20_Volume'] = (df[volume_col] / 1000).rolling(window=20).mean()
    if len(df['MA5_Volume'].dropna()) > 0 and len(df['MA20_Volume'].dropna()) > 0:
        latest_ma5_vol = df['MA5_Volume'].iloc[-1]
        latest_ma20_vol = df['MA20_Volume'].iloc[-1]
    else:
        latest_ma5_vol, latest_ma20_vol = 0, 0
    is_passed = (latest_ma5_vol >= threshold) and (latest_ma20_vol >= threshold)
    return df, is_passed, latest_ma5_vol, latest_ma20_vol

def draw_plotly_chart(df_chart, stock_name):
    df_chart['MA20'], df_chart['MA60'] = df_chart['Close'].rolling(20).mean(), df_chart['Close'].rolling(60).mean()
    std20 = df_chart['Close'].rolling(20).std()
    df_chart['Upper'], df_chart['Lower'] = df_chart['MA20'] + (2 * std20), df_chart['MA20'] - (2 * std20)
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name='K線', increasing_line_color='#ef5350', decreasing_line_color='#26a69a'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['MA60'], line=dict(color='#2962FF', width=2), name='季線(60MA)'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['Upper'], line=dict(color='#FFD700', width=1, dash='dot'), name='布林上軌'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df_chart.index, y=df_chart['Lower'], line=dict(color='#FFD700', width=1, dash='dot'), name='布林下軌'), row=1, col=1)
    colors = ['#ef5350' if row['Close'] >= row['Open'] else '#26a69a' for _, row in df_chart.iterrows()]
    fig.add_trace(go.Bar(x=df_chart.index, y=df_chart['Volume']/1000, marker_color=colors, name='成交量(張)'), row=2, col=1)
    fig.update_layout(title=f"【{stock_name}】戰情透視圖", yaxis_title="股價", yaxis2_title="成交量(張)", xaxis_rangeslider_visible=False, template="plotly_dark", height=600, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)

# 🏢 V10.0 核心功能：面試考核模組
def render_interview_panel(stock_name, current_price, heat_index):
    st.markdown("<div class='interview-panel'>", unsafe_allow_html=True)
    st.markdown(f"<h3>📝 董事長專屬：【{stock_name}】入職面試與核薪系統</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color:#8b92a5;'>請依據您查詢到的網路情報與新聞，親自為這名新員工打分。若有斷鏈缺料疑慮，請啟動一票否決。</p>", unsafe_allow_html=True)

    st.markdown("#### 🩺 四大維度健康檢查")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        tech_score = st.slider("📈 技術面體能 (雷達初試保證)", 0, 100, 90, disabled=True, key=f"tech_{stock_name}")
        calc_chip = 100 - (heat_index * 10) if heat_index != -1 else 50
        calc_chip = max(0, min(100, calc_chip))
        chip_score = st.slider("💰 籌碼面背景 (PTT 熱度反比)", 0, 100, calc_chip, disabled=True, key=f"chip_{stock_name}")
    with col_f2:
        fund_score = st.slider("📊 基本面履歷 (請依財報微調)", 0, 100, 70, key=f"fund_{stock_name}")
        news_score = st.slider("🚨 情報與供應鏈健康度 (防範缺料/假訂單)", 0, 100, 80, key=f"news_{stock_name}")

    st.markdown("#### 💼 聘用合約與 KPI 設定")
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        target_pct = st.number_input("🎯 期望報酬率 KPI (%)", min_value=1.0, max_value=100.0, value=20.0, step=1.0, key=f"tgt_{stock_name}")
    with col_k2:
        stop_pct = st.number_input("🛡️ 試用期淘汰風險 (%)", min_value=1.0, max_value=50.0, value=5.0, step=1.0, key=f"stp_{stock_name}")

    total_score = (tech_score * 0.3) + (chip_score * 0.3) + (fund_score * 0.2) + (news_score * 0.2)

    st.markdown("<hr style='border-color: #2b313f; margin: 15px 0;'>", unsafe_allow_html=True)

    if news_score < 50:
        st.error(f"🚨 【一票否決】情報與供應鏈疑慮過高 ({news_score}分)！疑似缺料或假新聞炒作，保護公司資產，拒絕錄用！")
    elif total_score >= 80:
        st.success(f"✅ 【體檢優異】綜合評分：{total_score:.1f}分。符合集團高標準，核准入職！")
        c1, c2 = st.columns(2)
        c1.metric("🎯 KPI 目標價 (停利)", f"{current_price * (1 + target_pct/100):.2f} 元", f"+{target_pct}% 產值")
        c2.metric("⚔️ 淘汰防線 (停損)", f"{current_price * (1 - stop_pct/100):.2f} 元", f"-{stop_pct}% 止血")
    else:
        st.warning(f"⚠️ 【體檢不合格】綜合評分：{total_score:.1f}分，未達 80 分錄用標準。建議暫緩錄用，繼續留校察看。")
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 7. 雙艙切換系統
# ==========================================
tab1, tab2 = st.tabs(["📡 第一艙：大範圍妖股雷達", "🎯 第二艙：自選股狙擊追蹤"])

# ------------------------------------------
# 第一艙：妖股雷達
# ------------------------------------------
with tab1:
    st.markdown("<div class='control-panel'>", unsafe_allow_html=True)
    
    st.radio("🎯 快速戰術套用 (點擊自動調整下方參數)：", list(TACTICS.keys()), horizontal=True, key="tactic_selector", on_change=apply_tactic)
    st.markdown("<hr style='border-color: #2b313f; margin-top: 5px; margin-bottom: 15px;'>", unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1: price_limit = st.slider("💰 銅板股上限 (現價)", 10.0, 150.0, st.session_state.p_limit, step=1.0, key="p_limit")
    with col2: vol_limit = st.slider("💧 邊緣人指數上限 (月均量)", 10, 5000, st.session_state.v_limit, step=10, key="v_limit") 
    with col3: min_today_vol = st.slider("🛡️ 活水底線 (今日成交)", 100, 3000, st.session_state.tv_limit, step=50, key="tv_limit") 
    with col4: power_multiplier = st.slider("🔥 核彈級爆發 (倍數)", 1.5, 15.0, st.session_state.pm_limit, step=0.5, key="pm_limit")

    st.markdown("<br>", unsafe_allow_html=True)
    col_chk1, col_chk2, col_chk3, col_chk4 = st.columns(4)
    with col_chk1: squeeze_filter = st.checkbox("✅ 啟動【布林帶壓縮】(<15%)", key="sqz_chk") 
    with col_chk2: ma_filter = st.checkbox("✅ 啟動【月季線糾結】(<3%)", key="ma_chk")
    with col_chk3: gap_filter = st.checkbox("✅ 啟動【主力跳空開高】(>2%)", key="gap_chk") 
    with col_chk4: washout_filter = st.checkbox("🚨 鎖定【極端洗盤換手】(振幅>12%)", key="wash_chk")

    st.markdown("<br>", unsafe_allow_html=True)
    col_batch, col_btn_run, col_btn_shower = st.columns([1.5, 1, 1.5])
    
    def format_batch_display(opt):
        return f"🟢 {opt} ── [✅ 已掃描]" if opt in scan_log else f"⚪ {opt}"
    current_idx = batch_options.index(st.session_state.locked_batch) if "locked_batch" in st.session_state and st.session_state.locked_batch in batch_options else 0
    with col_batch: 
        batch_choice = st.selectbox("請選擇本次派遣部隊：", batch_options, index=current_idx, format_func=format_batch_display)
        st.session_state.locked_batch = batch_choice

    with col_btn_run: 
        st.markdown("<br>", unsafe_allow_html=True)
        run_scan_btn = st.button("🚀 單一部隊掃描", use_container_width=True)
    with col_btn_shower: 
        st.markdown("<br>", unsafe_allow_html=True)
        shower_mode_btn = st.button("🛁 洗澡模式 (掛機全掃描分類)", use_container_width=True)
        
    st.markdown("</div>", unsafe_allow_html=True)

    def execute_radar_scan(batch_name, stock_list, twse_set, names_dict):
        if "全市場總掃描" in batch_name: target_stocks = stock_list
        elif "隨機" in batch_name: target_stocks = random.sample(stock_list, min(140, len(stock_list)))
        else:
            idx = batch_options.index(batch_name) - 1 
            target_stocks = stock_list[idx*BATCH_SIZE : (idx+1)*BATCH_SIZE]
            
        data_list = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        start_time = time.time()
        
        for i, stock_id in enumerate(target_stocks):
            elapsed_time = time.time() - start_time
            avg_time_per_stock = elapsed_time / (i + 1) if i > 0 else 0.05
            remaining_stocks = len(target_stocks) - (i + 1)
            eta_seconds = int(remaining_stocks * avg_time_per_stock)
            
            status_text.text(f"🧬 數據解析中... 標的: {stock_id} ({i+1}/{len(target_stocks)}) | ⏳ 預估剩餘時間: {eta_seconds} 秒")
            yf_ticker = f"{stock_id}.TWO" if (stock_id not in twse_set and twse_set) else f"{stock_id}.TW"
            
            try:
                time.sleep(random.uniform(0.01, 0.05))
                df_history = yf.Ticker(yf_ticker).history(period="4mo")
                if df_history.empty and twse_set:
                    yf_ticker = f"{stock_id}.TW" if yf_ticker.endswith(".TWO") else f"{stock_id}.TWO"
                    df_history = yf.Ticker(yf_ticker).history(period="4mo")
                
                if not df_history.empty and len(df_history) >= 60:
                    latest, yesterday = df_history.iloc[-1], df_history.iloc[-2]
                    close_p, today_vol = latest['Close'], latest['Volume'] / 1000
                    vol5 = df_history['Volume'].tail(5).mean() / 1000
                    vol20, ma20, ma60 = df_history['Volume'].tail(20).mean() / 1000, df_history['Close'].tail(20).mean(), df_history['Close'].tail(60).mean()
                    std20 = df_history['Close'].tail(20).std()
                    bandwidth = (((ma20 + 2*std20) - (ma20 - 2*std20)) / ma20) * 100 if ma20 > 0 else 999
                    ma_diff = (abs(ma20 - ma60) / ma60) * 100 if ma60 > 0 else 999
                    gap_up_pct = ((latest['Open'] - yesterday['Close']) / yesterday['Close']) * 100 if yesterday['Close'] > 0 else 0
                    is_fake = (latest['High'] - max(latest['Open'], close_p)) > abs(close_p - latest['Open'])
                    
                    amplitude = ((latest['High'] - latest['Low']) / yesterday['Close']) * 100 if yesterday['Close'] > 0 else 0
                    is_washout = (amplitude >= 12.0) and (today_vol >= vol5 * 3) and (close_p >= (latest['High'] + latest['Low']) / 2)
                    
                    data_list.append({
                        '股票代號': stock_id, '股票名稱': names_dict.get(stock_id, "未知"), '現價(元)': close_p, 
                        '今日漲跌(%)': round(((close_p - yesterday['Close'])/yesterday['Close'])*100, 2),
                        '日內振幅(%)': round(amplitude, 2),
                        '五日均量(張)': round(vol5, 0),
                        '月均量(20日)': round(vol20, 0), '今日成交(張)': round(today_vol, 0),
                        '月量爆發倍數': round(today_vol / vol20, 1) if vol20 > 0 else 0,
                        '跳空缺口(%)': round(gap_up_pct, 2), '布林帶寬(%)': round(bandwidth, 2),
                        '均線糾結(%)': round(ma_diff, 2), 
                        '季線位置': round(ma60, 2),
                        '強力洗盘訊號': '🚨 觸發' if is_washout else '-',
                        '_is_fake': is_fake,
                        '_is_washout': is_washout
                    })
            except: pass
            progress_bar.progress((i + 1) / len(target_stocks))
            
        status_text.empty(); progress_bar.empty()
        return pd.DataFrame(data_list)

    if run_scan_btn:
        with st.spinner(f"🚀 啟動單一部隊掃描中..."):
            df_result = execute_radar_scan(batch_choice, pure_stocks, twse_set, stock_names_dict)
            st.session_state.master_df = df_result
            st.session_state.master_batch = batch_choice
            st.session_state.is_shower_mode = False
            scan_log[batch_choice] = datetime.now().strftime("%Y/%m/%d %H:%M")
            save_scan_log(scan_log)
            st.rerun()

    if shower_mode_btn:
        with st.spinner(f"🛁 洗澡模式啟動！正在進行全市場總掃描，請安心去洗澡，約需 10 分鐘..."):
            df_result = execute_radar_scan("🌟 全市場總掃描 (約需 3~5 分鐘，建議使用)", pure_stocks, twse_set, stock_names_dict)
            st.session_state.master_df = df_result
            st.session_state.master_batch = "🌟 全市場總掃描 (約需 3~5 分鐘，建議使用)"
            st.session_state.is_shower_mode = True 
            scan_log["🌟 全市場總掃描 (約需 3~5 分鐘，建議使用)"] = datetime.now().strftime("%Y/%m/%d %H:%M")
            save_scan_log(scan_log)
            st.rerun()

    if "master_df" in st.session_state:
        df_market = st.session_state.master_df
        
        # 🚀 V9.9 全自動情報暗殺系統 
        def apply_mask_and_style(df, cfg):
            mask = (df['現價(元)'] <= cfg['p']) & (df['今日成交(張)'] >= cfg['tv']) & \
                   (df['月均量(20日)'] <= cfg['v']) & (df['月量爆發倍數'] >= cfg['pm']) & \
                   (df['_is_fake'] == False) & (df['現價(元)'] > df['季線位置']) & \
                   (df['五日均量(張)'] >= 500) & (df['月均量(20日)'] >= 500)
                   
            if cfg['sqz']: mask = mask & (df['布林帶寬(%)'] <= 15)
            if cfg['ma']: mask = mask & (df['均線糾結(%)'] <= 3)
            if cfg['gap']: mask = mask & (df['跳空缺口(%)'] >= 2.0)
            if cfg['wash']: mask = mask & (df['_is_washout'] == True)
            
            res_df = df[mask].drop(columns=['_is_fake', '_is_washout']).drop_duplicates(subset=['股票代號']).reset_index(drop=True)
            
            if not res_df.empty:
                with st.spinner("🕵️‍♂️ 啟動終極情報暗殺：自動探測 PTT 散戶熱度過濾中..."):
                    ptt_indices = []
                    for _, row in res_df.iterrows():
                        idx = get_ptt_shoeshine_index(row['股票名稱'])
                        ptt_indices.append(idx)
                        time.sleep(0.5)
                        
                    res_df['PTT熱度'] = ptt_indices
                    res_df = res_df[(res_df['PTT熱度'] >= 0) & (res_df['PTT熱度'] <= 2)].reset_index(drop=True)
                    
                    if res_df.empty: return None
                        
                    return res_df.style.format({
                        '現價(元)': "{:.2f}", '今日漲跌(%)': "{:.2f}%", '日內振幅(%)': "{:.2f}%", 
                        '五日均量(張)': "{:,.0f}", '月均量(20日)': "{:,.0f}", '今日成交(張)': "{:,.0f}", 
                        '月量爆發倍數': "{:.1f}x", '跳空缺口(%)': "{:.2f}%", 
                        '布林帶寬(%)': "{:.2f}%", '均線糾結(%)': "{:.2f}%", '季線位置': "{:.2f}",
                        'PTT熱度': "{:.0f} 篇"
                    }).background_gradient(subset=['月量爆發倍數', '日內振幅(%)'], cmap='Purples')
            return None

        if st.session_state.get("is_shower_mode", False):
            st.success("🛁 洗澡模式掃描完畢！以下為全市場套用四大戰術的分類結果 (僅顯示 PTT ≦ 2 篇之完美潛艦)：")
            st.write("---")
            t1, t2, t3, t4 = st.tabs(["🌊 戰術一：深海潛艦", "🌋 戰術二：大怒神", "⚡ 戰術三：閃電戰", "🐂 戰術四：老牛翻身"])
            with t1:
                res1 = apply_mask_and_style(df_market, TACTICS["🌊 戰術一：深海潛艦 (經典起漲)"])
                if res1 is not None: st.dataframe(res1, use_container_width=True)
                else: st.info("🛡️ 今日無符合條件之標的。")
            with t2:
                res2 = apply_mask_and_style(df_market, TACTICS["🌋 戰術二：大怒神 (極端洗盤)"])
                if res2 is not None: st.dataframe(res2, use_container_width=True)
                else: st.info("🛡️ 今日無符合條件之標的。")
            with t3:
                res3 = apply_mask_and_style(df_market, TACTICS["⚡ 戰術三：閃電戰 (跳空突破)"])
                if res3 is not None: st.dataframe(res3, use_container_width=True)
                else: st.info("🛡️ 今日無符合條件之標的。")
            with t4:
                res4 = apply_mask_and_style(df_market, TACTICS["🐂 戰術四：老牛翻身 (穩健推升)"])
                if res4 is not None: st.dataframe(res4, use_container_width=True)
                else: st.info("🛡️ 今日無符合條件之標的。")
        else:
            cfg_custom = {
                'p': price_limit, 'v': vol_limit, 'tv': min_today_vol, 'pm': power_multiplier,
                'sqz': squeeze_filter, 'ma': ma_filter, 'gap': gap_filter, 'wash': washout_filter
            }
            res_custom = apply_mask_and_style(df_market, cfg_custom)
            if res_custom is not None:
                st.write("---")
                st.dataframe(res_custom, use_container_width=True)
            else:
                st.info("🛡️ 終極情報暗殺完畢：無符合設定且 PTT 討論低於 2 篇之標的。寧可空手，絕不追高！")
            
        st.write("---")
        st.markdown("<h2>📊 X 光透視與情報探測 (完整資料庫選單)</h2>", unsafe_allow_html=True)
        available_stocks = df_market[df_market['現價(元)'] > df_market['季線位置']] 
        stock_options = [f"{row['股票代號']} - {row['股票名稱']}" for _, row in available_stocks.iterrows()]
        
        if stock_options:
            selected_stock_str = st.selectbox("🎯 選擇欲進行深度 X 光探測的標的：", stock_options, key="sel1")
            
            try:
                selected_id, selected_name = selected_stock_str.split(" - ")[0], selected_stock_str.split(" - ")[1]
                current_price = available_stocks[available_stocks['股票代號'] == selected_id]['現價(元)'].values[0]
                max_buyable_lots = int(MAX_EXPOSURE // (current_price * 1000))
                
                with st.spinner(f"🕵️‍♂️ 調閱情報檔案..."): 
                    heat_index = get_ptt_shoeshine_index(selected_name)
                
                st.markdown("<div class='shoeshine-panel'>", unsafe_allow_html=True)
                st.markdown("#### 🕵️‍♂️ 擦鞋童警報器 (PTT 散戶狂熱指數)")
                if heat_index == -1: st.warning("⚠️ 情報網連線異常，無法取得 PTT 資料。")
                elif heat_index <= 2: st.success(f"🟢 **【完美潛伏期】** PTT 討論僅 {heat_index} 篇！主力偷偷吃貨中。")
                elif heat_index <= 9: st.warning(f"🟡 **【發酵警戒區】** PTT 討論達 {heat_index} 篇。消息走漏，請嚴守紀律。")
                else: st.error(f"🔴 **【擦鞋童核爆區】** PTT 討論高達 {heat_index} 篇！禁止買進！")
                st.markdown("</div>", unsafe_allow_html=True)
                
                if max_buyable_lots >= 1: 
                    st.success(f"⚖️ **資金審批通過：** 現價 {current_price:.2f} 元，最高授權購買 **{max_buyable_lots} 張**。")
                else: 
                    st.error(f"⚖️ **🚨 資金拒絕授權：** 現價 {current_price:.2f} 元，買一張已超標 ({int(MAX_EXPOSURE):,} 元)！")
                
                with st.spinner(f"展開 K 線圖..."):
                    is_otc = False if not twse_set else (selected_id not in twse_set)
                    df_chart = get_kline_data(selected_id, is_otc)
                    
                    if not df_chart.empty: 
                        processed_df, passed_filter, current_ma5, current_ma20 = add_liquidity_filter(df_chart, volume_col='Volume', threshold=500)
                        
                        if not passed_filter:
                            st.error(f"⚠️ 流動性警報：未達雙重 500 張門檻。系統已攔截圖表。")
                        else:
                            st.success(f"✅ 流動性審查通過。")
                            draw_plotly_chart(processed_df, selected_name)
                            
                            # 🏢 植入 V10.0 面試考核模組
                            render_interview_panel(selected_name, current_price, heat_index)
                    else:
                        st.warning("⚠️ 系統提示：無法取得該檔股票的歷史 K 線資料。")

            except Exception as e:
                st.error(f"⚠️ 解析該檔標的時發生系統異常：{str(e)}")

# ------------------------------------------
# 第二艙：單點狙擊追蹤
# ------------------------------------------
with tab2:
    st.markdown("<div class='control-panel'>", unsafe_allow_html=True)
    st.markdown("<h3>🎯 自選股獨立情報探測</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color:#8b92a5;'>總司令，請在此直接輸入您關注的股票代號。系統將繞過雷達，瞬間回傳情報與 K 線圖，並啟動面試評估系統。</p>", unsafe_allow_html=True)
    
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        target_code = st.text_input("輸入股票代號：", placeholder="例如: 2483")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        search_btn = st.button("🚀 啟動單點探測", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if search_btn and target_code:
        target_code = str(target_code).strip()
        if target_code in stock_names_dict:
            target_name = stock_names_dict[target_code]
            st.markdown(f"<h2>📊 【{target_code} {target_name}】 專案追蹤報告</h2>", unsafe_allow_html=True)
            
            try:
                with st.spinner(f"🕵️‍♂️ 探測 {target_name} 的散戶熱度..."):
                    heat_index = get_ptt_shoeshine_index(target_name)
                
                st.markdown("<div class='shoeshine-panel'>", unsafe_allow_html=True)
                st.markdown("#### 🕵️‍♂️ 擦鞋童警報器 (PTT 散戶狂熱指數)")
                if heat_index == -1: st.warning("⚠️ 情報網連線異常。")
                elif heat_index <= 2: st.success(f"🟢 **【完美潛伏期】** PTT 討論僅 {heat_index} 篇！")
                elif heat_index <= 9: st.warning(f"🟡 **【發酵警戒區】** PTT 討論達 {heat_index} 篇。消息走漏，請嚴守紀律。")
                else: st.error(f"🔴 **【擦鞋童核爆區】** PTT 討論高達 {heat_index} 篇！禁止買進！")
                st.markdown("</div>", unsafe_allow_html=True)
                
                with st.spinner(f"展開 {target_name} K 線圖..."):
                    is_otc = False if not twse_set else (target_code not in twse_set)
                    df_chart = get_kline_data(target_code, is_otc)
                    
                    if not df_chart.empty:
                        current_price = df_chart.iloc[-1]['Close']
                        max_buyable_lots = int(MAX_EXPOSURE // (current_price * 1000))
                        
                        if max_buyable_lots >= 1: 
                            st.success(f"⚖️ **資金審批通過：** 現價 {current_price:.2f} 元，最高授權購買 **{max_buyable_lots} 張**。")
                        else: 
                            st.error(f"⚖️ **🚨 資金拒絕授權：** 現價 {current_price:.2f} 元，買一張已超標 ({int(MAX_EXPOSURE):,} 元)！")
                        
                        processed_df, passed_filter, current_ma5, current_ma20 = add_liquidity_filter(df_chart, volume_col='Volume', threshold=500)
                        
                        if not passed_filter:
                            st.error(f"⚠️ 流動性警報：未達雙重 500 張門檻。系統已攔截圖表。")
                        else:
                            st.success(f"✅ 流動性審查通過。")
                            draw_plotly_chart(processed_df, target_name)
                            
                            # 🏢 植入 V10.0 面試考核模組
                            render_interview_panel(target_name, current_price, heat_index)
                    else:
                        st.warning("⚠️ 系統提示：無法取得該檔股票的歷史 K 線資料。")
                        
            except Exception as e:
                st.error(f"⚠️ 解析該檔標的時發生系統異常：{str(e)}")
        else:
            st.error(f"❌ 查無此代號：{target_code}，請確認是否為正規 4 碼上市櫃股票。")
