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
st.set_page_config(page_title="台股妖股雷達 V9.5 | 雙艙旗艦版", layout="wide", page_icon="📡")

# ==========================================
# 2. 戰情日誌記憶系統
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
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { background-color: #1e293b; border-radius: 8px 8px 0px 0px; padding: 10px 20px; color: #8b92a5; border: 1px solid #2b313f; border-bottom: none; }
    .stTabs [aria-selected="true"] { background-color: #FF00FF !important; color: white !important; font-weight: bold; }
    .stSelectbox label, .stTextInput label { color: #3b82f6 !important; font-weight: bold; font-size: 1.1rem !important; }
    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div { background-color: #1e293b !important; border: 1px solid #3b82f6 !important; }
    div[data-baseweb="select"] *, div[data-baseweb="input"] * { color: #00FF41 !important; font-weight: bold !important; }
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
st.markdown("<h1>⚡ 台股妖股雷達 <span style='color: #FFD700;'>V9.5</span> <span style='font-size: 0.5em; color: #8b92a5;'>(雙艙旗艦版)</span></h1>", unsafe_allow_html=True)
st.markdown("<div class='risk-panel'>", unsafe_allow_html=True)
st.markdown("<h3>🏛️ 瑋婷總監 - 國庫資金防護網</h3>", unsafe_allow_html=True)
rc1, rc2, rc3 = st.columns(3)
TOTAL_CAPITAL = 1170000
MAX_RISK_PCT = 0.05 # 放寬為 5%
MAX_EXPOSURE = TOTAL_CAPITAL * MAX_RISK_PCT
rc1.metric("🛡️ 大本營總戰備資金", f"NT$ {TOTAL_CAPITAL:,}")
rc2.metric("⚠️ 單檔極限曝險 (5%)", f"NT$ {int(MAX_EXPOSURE):,}")
rc3.metric("🚦 系統狀態", "雙艙作戰系統上線", delta="滿配防護中", delta_color="normal")
st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 6. 情報工具箱 (快取函式)
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

# ==========================================
# 7. 雙艙切換系統 (V9.5 核心)
# ==========================================
tab1, tab2 = st.tabs(["📡 第一艙：大範圍妖股雷達", "🎯 第二艙：自選股狙擊追蹤"])

# ------------------------------------------
# 第一艙：妖股雷達 (原有 V9.4 掃描功能)
# ------------------------------------------
with tab1:
    st.markdown("<div class='control-panel'>", unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1: price_limit = st.slider("💰 銅板股上限 (現價)", 10.0, 100.0, 50.0, step=1.0)
    with col2: vol_limit = st.slider("💧 邊緣人指數 (月均量)", 10, 2000, 1000, step=10) 
    with col3: min_today_vol = st.slider("🛡️ 活水底線 (今日成交)", 100, 2000, 300, step=50) 
    with col4: power_multiplier = st.slider("🔥 核彈級爆發 (倍數)", 2.0, 15.0, 3.0, step=0.5)

    st.markdown("<hr style='border-color: #2b313f; margin-top: 10px; margin-bottom: 10px;'>", unsafe_allow_html=True)
    col_chk1, col_chk2, col_chk3, col_chk4 = st.columns(4)
    with col_chk1: squeeze_filter = st.checkbox("✅ 啟動【布林帶壓縮】(<15%)", value=False, key="sqz") 
    with col_chk2: ma_filter = st.checkbox("✅ 啟動【月季線糾結】(<3%)", value=True, key="ma")
    with col_chk3: gap_filter = st.checkbox("✅ 啟動【主力跳空開高】(>2%)", value=False, key="gap") 
    with col_chk4: washout_filter = st.checkbox("🚨 鎖定【極端洗盤換手】(振幅>12%)", value=False, key="wash")
    st.markdown("<div style='color:#8b92a5; font-size: 0.9em; margin-top:10px;'>💡 提示：勾選並調整滑桿，下方名單會【瞬間】套用篩選！</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    col_batch, col_btn_run, col_btn_reset = st.columns([2, 1.5, 1.5])
    
    def format_batch_display(opt):
        return f"🟢 {opt} ── [✅ 已掃描]" if opt in scan_log else f"⚪ {opt}"
    current_idx = batch_options.index(st.session_state.locked_batch) if "locked_batch" in st.session_state and st.session_state.locked_batch in batch_options else 0
    with col_batch: 
        batch_choice = st.selectbox("請選擇本次派遣部隊：", batch_options, index=current_idx, format_func=format_batch_display)
        st.session_state.locked_batch = batch_choice

    with col_btn_run: 
        st.markdown("<br>", unsafe_allow_html=True)
        run_scan_btn = st.button("🚀 發起 V9.5 實彈掃描", use_container_width=True)
    with col_btn_reset: 
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🧹 清除快取 (重置系統)", use_container_width=True, key="btn_clear1"): 
            st.cache_data.clear()
            st.session_state.clear()
            st.rerun()
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
                    
                    # 日內振幅監控邏輯
                    amplitude = ((latest['High'] - latest['Low']) / yesterday['Close']) * 100 if yesterday['Close'] > 0 else 0
                    # 判斷是否為強力洗盤：振幅 >= 12%、量能 >= 5日均量3倍、收盤價 >= 當日均價
                    is_washout = (amplitude >= 12.0) and (today_vol >= vol5 * 3) and (close_p >= (latest['High'] + latest['Low']) / 2)
                    
                    data_list.append({
                        '股票代號': stock_id, '股票名稱': names_dict.get(stock_id, "未知"), '現價(元)': close_p, 
                        '今日漲跌(%)': round(((close_p - yesterday['Close'])/yesterday['Close'])*100, 2),
                        '日內振幅(%)': round(amplitude, 2),
                        '月均量(20日)': round(vol20, 0), '今日成交(張)': round(today_vol, 0),
                        '月量爆發倍數': round(today_vol / vol20, 1) if vol20 > 0 else 0,
                        '跳空缺口(%)': round(gap_up_pct, 2), '布林帶寬(%)': round(bandwidth, 2),
                        '均線糾結(%)': round(ma_diff, 2), 
                        '強力洗盤訊號': '🚨 觸發' if is_washout else '-',
                        '_is_fake': is_fake,
                        '_is_washout': is_washout
                    })
            except: pass
            progress_bar.progress((i + 1) / len(target_stocks))
            
        status_text.empty(); progress_bar.empty()
        return pd.DataFrame(data_list)

    if run_scan_btn:
        with st.spinner(f"🚀 啟動 V9.5 主引擎掃描中..."):
            df_result = execute_radar_scan(batch_choice, pure_stocks, twse_set, stock_names_dict)
            st.session_state.master_df = df_result
            st.session_state.master_batch = batch_choice
            scan_log[batch_choice] = datetime.now().strftime("%Y/%m/%d %H:%M")
            save_scan_log(scan_log)
            st.rerun()

    if "master_df" in st.session_state and st.session_state.master_batch == batch_choice:
        df_market = st.session_state.master_df
        mask = (df_market['現價(元)'] <= price_limit) & (df_market['今日成交(張)'] >= min_today_vol) & (df_market['月均量(20日)'] <= vol_limit) & (df_market['月量爆發倍數'] >= power_multiplier) & (df_market['_is_fake'] == False)
        if squeeze_filter: mask = mask & (df_market['布林帶寬(%)'] <= 15)
        if ma_filter: mask = mask & (df_market['均線糾結(%)'] <= 3)
        if gap_filter: mask = mask & (df_market['跳空缺口(%)'] >= 2.0)
        if washout_filter: mask = mask & (df_market['_is_washout'] == True)
            
        demon_stocks = df_market[mask].drop(columns=['_is_fake', '_is_washout']).drop_duplicates(subset=['股票代號']).reset_index(drop=True)
        
        if not demon_stocks.empty:
            st.write("---")
            styled_df = demon_stocks.style.format({
                '現價(元)': "{:.2f}", '今日漲跌(%)': "{:.2f}%", '日內振幅(%)': "{:.2f}%", 
                '月均量(20日)': "{:,.0f}", '今日成交(張)': "{:,.0f}", 
                '月量爆發倍數': "{:.1f}x", '跳空缺口(%)': "{:.2f}%", 
                '布林帶寬(%)': "{:.2f}%", '均線糾結(%)': "{:.2f}%"
            }).background_gradient(subset=['月量爆發倍數', '日內振幅(%)'], cmap='Purples')
            st.dataframe(styled_df, use_container_width=True)
            
            st.write("---")
            st.markdown("<h2>📊 X 光透視與情報探測</h2>", unsafe_allow_html=True)
            stock_options = [f"{row['股票代號']} - {row['股票名稱']}" for _, row in demon_stocks.iterrows()]
            selected_stock_str = st.selectbox("🎯 選擇標的：", stock_options, key="sel1")
            selected_id, selected_name = selected_stock_str.split(" - ")[0], selected_stock_str.split(" - ")[1]
            
            current_price = demon_stocks[demon_stocks['股票代號'] == selected_id]['現價(元)'].values[0]
            max_buyable_lots = int(MAX_EXPOSURE // (current_price * 1000))
            
            with st.spinner(f"🕵️‍♂️ 調閱情報檔案..."): heat_index = get_ptt_shoeshine_index(selected_name)
            
            st.markdown("<div class='shoeshine-panel'>", unsafe_allow_html=True)
            st.markdown("#### 🕵️‍♂️ 擦鞋童警報器 (PTT 散戶狂熱指數)")
            if heat_index == -1: st.warning("⚠️ 情報網連線異常。")
            elif heat_index <= 2: st.success(f"🟢 **【完美潛伏期】** PTT 討論僅 {heat_index} 篇！主力偷偷吃貨中。")
            elif heat_index <= 9: st.warning(f"🟡 **【發酵警戒區】** PTT 討論達 {heat_index} 篇。消息走漏，請嚴守紀律。")
            else: st.error(f"🔴 **【擦鞋童核爆區】** PTT 討論高達 {heat_index} 篇！禁止買進！")
            st.markdown("</div>", unsafe_allow_html=True)
            
            if max_buyable_lots >= 1: st.success(f"⚖️ **財務總監審批通過：** 現價 {current_price} 元，最高授權購買 **{max_buyable_lots} 張**。")
            else: st.error(f"⚖️ **🚨 財務總監拒絕授權：** 已超標 (58,500 元)！")
            
            with st.spinner(f"展開 K 線圖..."):
                is_otc = False if not twse_set else (selected_id not in twse_set)
                df_chart = get_kline_data(selected_id, is_otc)
                if not df_chart.empty: draw_plotly_chart(df_chart, selected_name)

            st.write("---")
            if st.button("☁️ 總監核准，將清單備份至【雲端戰情庫】", use_container_width=True):
                with st.spinner("📡 正在上傳..."):
                    try:
                        import gspread
                        client = gspread.service_account(filename="google_key.json")
                        sheet = client.open("妖股雷達_戰情觀測庫").sheet1
                        upload_data = []
                        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                        for index, row in demon_stocks.iterrows():
                            row_heat = get_ptt_shoeshine_index(row['股票名稱'])
                            heat_note = f"PTT熱度: {row_heat} 篇" if row_heat >= 0 else "PTT熱度: 異常"
                            if row_heat > 9: heat_note += " (高危險)"
                            upload_data.append([
                                current_time, str(row['股票名稱']), str(row['股票代號']), 
                                float(row['現價(元)']), float(row['今日漲跌(%)']), float(row['日內振幅(%)']),
                                int(row['月均量(20日)']), int(row['今日成交(張)']), float(row['月量爆發倍數']), 
                                float(row['跳空缺口(%)']), float(row['布林帶寬(%)']), float(row['均線糾結(%)']), 
                                str(row['強力洗盤訊號']), heat_note
                            ])
                        sheet.append_rows(upload_data, value_input_option='USER_ENTERED')
                        st.success(f"✅ 成功將情報備份至雲端！")
                        st.balloons()
                    except Exception as e: st.error(f"❌ 上傳失敗，錯誤碼：{e}")
        else: st.info("🛡️ 無符合設定的股票。請放寬條件！")
    elif "master_df" in st.session_state: st.info("⚠️ 您已切換部隊，請點擊【🚀 發起實彈掃描】！")

# ------------------------------------------
# 第二艙：單點狙擊追蹤 (V9.5 全新獨立系統)
# ------------------------------------------
with tab2:
    st.markdown("<div class='control-panel'>", unsafe_allow_html=True)
    st.markdown("<h3>🎯 自選股獨立情報探測</h3>", unsafe_allow_html=True)
    st.markdown("<p style='color:#8b92a5;'>總司令，請在此直接輸入您關注的股票代號 (如: 6136)。系統將繞過雷達，瞬間回傳該檔標的之 PTT 情報與 K 線圖。</p>", unsafe_allow_html=True)
    
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        target_code = st.text_input("輸入股票代號：", placeholder="例如: 6136")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        search_btn = st.button("🚀 啟動單點探測", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if search_btn and target_code:
        target_code = str(target_code).strip()
        if target_code in stock_names_dict:
            target_name = stock_names_dict[target_code]
            st.markdown(f"<h2>📊 【{target_code} {target_name}】 專案追蹤報告</h2>", unsafe_allow_html=True)
            
            # PTT 探測器
            with st.spinner(f"🕵️‍♂️ 探測 {target_name} 的散戶熱度..."):
                heat_index = get_ptt_shoeshine_index(target_name)
            
            st.markdown("<div class='shoeshine-panel'>", unsafe_allow_html=True)
            st.markdown("#### 🕵️‍♂️ 擦鞋童警報器 (PTT 散戶狂熱指數)")
            if heat_index == -1: st.warning("⚠️ 情報網連線異常。")
            elif heat_index <= 2: st.success(f"🟢 **【完美潛伏期】** PTT 討論僅 {heat_index} 篇！")
            elif heat_index <= 9: st.warning(f"🟡 **【發酵警戒區】** PTT 討論達 {heat_index} 篇。消息走漏，請嚴守紀律。")
            else: st.error(f"🔴 **【擦鞋童核爆區】** PTT 討論高達 {heat_index} 篇！總監協議：禁止買進！")
            st.markdown("</div>", unsafe_allow_html=True)
            
            # K線圖
            with st.spinner(f"展開 {target_name} K 線圖..."):
                is_otc = False if not twse_set else (target_code not in twse_set)
                df_chart = get_kline_data(target_code, is_otc)
                
                if not df_chart.empty:
                    # 抓取最新現價進行風控計算
                    current_price = df_chart.iloc[-1]['Close']
                    max_buyable_lots = int(MAX_EXPOSURE // (current_price * 1000))
                    
                    if max_buyable_lots >= 1: st.success(f"⚖️ **財務總監審批通過：** 現價 {current_price:.2f} 元，最高授權購買 **{max_buyable_lots} 張**。")
                    else: st.error(f"⚖️ **🚨 財務總監拒絕授權：** 現價 {current_price:.2f} 元，買一張已超標 (58,500 元)！")
                    
                    draw_plotly_chart(df_chart, target_name)
                else:
                    st.error("無法取得該檔股票的歷史 K 線資料。")
        else:
            st.error(f"❌ 查無此代號：{target_code}，請確認是否為正規 4 碼上市櫃股票。")
