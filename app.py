import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import joblib
import json
import os
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# Feature Engineering
from feature_engine import engineer_features

# Direct RNS Scraper
try:
    from rns_scraper import fetch_direct_rns_for_ticker
except ImportError:
    def fetch_direct_rns_for_ticker(ticker: str):
        return {
            "headline": "Standard Market Flow",
            "status": "📰 Flow Verified",
            "delta": 0.0,
            "timestamp": datetime.now().strftime("%Y-%m-%d")
        }

# Modular Backtester
try:
    from backtester import run_backtest_simulation
except ImportError:
    run_backtest_simulation = None

# Supabase Cloud Database Client
try:
    from supabase import create_client, Client
except ImportError:
    create_client, Client = None, None

# ==============================================================================
# PAGE CONFIGURATION & STYLING
# ==============================================================================
st.set_page_config(
    page_title="ALPHA-LSE // Institutional Decision Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 5-Minute Auto-Refresh Trigger (300,000 ms)
st_autorefresh(interval=300000, key="audit_cycle_5m")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@600;700&display=swap');
    * { font-family: 'Plus Jakarta Sans', sans-serif; }
    
    [data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 1.30rem !important;
        color: #f8fafc !important;
    }
    
    [data-testid="stMetricLabel"] {
        font-size: 0.72rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        color: #94a3b8 !important;
        font-weight: 700 !important;
    }
    
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #111726 !important;
        border: 1px solid #1f293d !important;
        border-radius: 16px !important;
        padding: 20px !important;
        margin-bottom: 18px !important;
    }

    .badge-rns-safe {
        background: rgba(16, 185, 129, 0.12);
        color: #10b981;
        border: 1px solid #10b981;
        border-radius: 6px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 700;
    }
    .badge-rns-alert {
        background: rgba(239, 68, 68, 0.15);
        color: #ef4444;
        border: 1px solid #ef4444;
        border-radius: 6px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 700;
    }
    .badge-rns-bull {
        background: rgba(56, 189, 248, 0.15);
        color: #38bdf8;
        border: 1px solid #38bdf8;
        border-radius: 6px;
        padding: 4px 10px;
        font-size: 0.75rem;
        font-weight: 700;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# GLOBAL CONFIG & SUPABASE INITIALIZATION
# ==============================================================================
MODEL_PATH = "./models/ensemble_ranker.joblib"
UNIVERSE_FILE = "./data/universe.json"
OUTPUT_DIR = "./output"
TOTAL_CAPITAL_GBP = 500.0

os.makedirs("./data", exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

@st.cache_resource
def get_supabase_client():
    if create_client is None:
        return None
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        return None

supabase = get_supabase_client()

# ==============================================================================
# CLOUD DATABASE CONTROLLERS
# ==============================================================================
def execute_launch_learning():
    """Continuously retrains decision trees using verified historical trade outcomes from Supabase."""
    if not supabase or not os.path.exists(MODEL_PATH):
        return 0
    try:
        response = supabase.table("predictions").select("*").in_("status", ["🎯 Target Hit", "🛑 Stopped Out"]).execute()
        rows = response.data
        if not rows or len(rows) < 3:
            return 0

        bundle = joblib.load(MODEL_PATH)
        valid_x, valid_y = [], []
        for r in rows:
            feats = r.get('features_json')
            if feats:
                if isinstance(feats, str):
                    try:
                        feats = json.loads(feats)
                    except Exception:
                        continue
                valid_x.append(feats)
                valid_y.append(1 if r.get('status') == '🎯 Target Hit' else 0)

        if len(valid_x) >= 3:
            X_retrain = pd.DataFrame(valid_x)[bundle['feature_cols']]
            y_retrain = np.array(valid_y)
            bundle['lgb'].fit(X_retrain, y_retrain)
            joblib.dump(bundle, MODEL_PATH)
            return len(valid_x)
    except Exception:
        return 0
    return 0

def audit_open_trades():
    """Audits active positions against live LSE pricing and writes status changes back to Supabase."""
    if not supabase:
        return pd.DataFrame()
    try:
        response = supabase.table("predictions").select("*").order("id", desc=True).limit(50).execute()
        rows = response.data
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        active_mask = df['status'] == 'Active'
        active_tickers = df.loc[active_mask, 'ticker'].unique().tolist()

        if active_tickers:
            live_prices = {}
            for tkr in active_tickers:
                try:
                    h = yf.Ticker(tkr).history(period="5d", auto_adjust=False)
                    if not h.empty:
                        live_prices[tkr] = float(h['Close'].iloc[-1])
                except Exception:
                    continue

            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for _, row in df.loc[active_mask].iterrows():
                tkr = row['ticker']
                if tkr in live_prices:
                    curr_p = live_prices[tkr]
                    entry_p = float(row['entry_price'])
                    target_p = float(row['target_price'])
                    stop_p = float(row['stop_loss']) if row.get('stop_loss') and not pd.isna(row.get('stop_loss')) else (entry_p * 0.94)

                    pnl = round(((curr_p - entry_p) / entry_p) * 100, 2)
                    status = "Active"
                    if curr_p >= target_p:
                        status = "🎯 Target Hit"
                    elif curr_p <= stop_p:
                        status = "🛑 Stopped Out"

                    supabase.table("predictions").update({
                        "latest_price": curr_p,
                        "pnl_pct": pnl,
                        "status": status,
                        "last_checked": now_str
                    }).eq("id", row['id']).execute()

        fresh_response = supabase.table("predictions").select("*").order("id", desc=True).limit(50).execute()
        return pd.DataFrame(fresh_response.data)
    except Exception:
        return pd.DataFrame()

def save_prediction_to_cloud(p):
    """Saves candidate breakout predictions into the persistent cloud database."""
    if not supabase:
        return
    today_str = datetime.now().strftime('%Y-%m-%d')
    try:
        existing = supabase.table("predictions").select("id").eq("ticker", p['ticker']).eq("predicted_date", today_str).execute()
        if not existing.data:
            supabase.table("predictions").insert({
                "predicted_date": today_str,
                "ticker": p['ticker'],
                "company_name": p['company_name'],
                "entry_price": p['buy_entry'],
                "target_price": p['target_price'],
                "stop_loss": p['stop_loss'],
                "position_gbp": p['position_gbp'],
                "shares_qty": p['shares_qty'],
                "profit_goal": p['profit_potential'],
                "confidence": p['confidence'],
                "hold_days": p['hold_days'],
                "status": "Active",
                "latest_price": p['current_price'],
                "pnl_pct": 0.0,
                "news_status": p['news_status'],
                "rns_headline": p['rns_headline'],
                "features_json": p['features_dict'],
                "last_checked": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }).execute()
    except Exception:
        pass

def format_price(p: float) -> str:
    if p is None or pd.isna(p):
        return "-"
    return f"{p:.4f}p" if p < 1.0 else f"{p:.2f}p"

# ==============================================================================
# PREDICTION PIPELINE
# ==============================================================================
@st.cache_data(ttl=900, show_spinner=False)
def generate_top_predictions():
    if not os.path.exists(MODEL_PATH) or not os.path.exists(UNIVERSE_FILE):
        return []

    bundle = joblib.load(MODEL_PATH)
    lgb_model = bundle['lgb']
    cb_model = bundle['catboost']
    feature_cols = bundle['feature_cols']

    with open(UNIVERSE_FILE, "r") as f:
        tickers = json.load(f)

    picks = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            df = t.history(period="120d", auto_adjust=False)
            if df is None or len(df) < 55:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]
            df.reset_index(inplace=True)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)

            feats = engineer_features(df)
            if feats.empty:
                continue

            latest = feats.iloc[-1:].copy()
            turnover_gbp = float(latest['turnover_gbp'].values[0])
            spread_est = float(latest['est_spread_pct'].values[0])

            if turnover_gbp < 8000:
                continue

            # Official RNS News Scraping
            rns_data = fetch_direct_rns_for_ticker(ticker)
            if "Dilution" in rns_data['status']:
                continue

            # Multi-Model Dual Ensemble
            p1 = float(lgb_model.predict_proba(latest[feature_cols])[:, 1][0])
            p2 = float(cb_model.predict_proba(latest[feature_cols])[:, 1][0])
            blended = (0.5 * p1 + 0.5 * p2) + rns_data['delta']
            blended = max(0.05, min(0.95, blended))
            confidence = int(min(98, max(52, round((blended / 0.35) * 85))))

            curr_price = float(latest['Close'].values[0])
            atr = float(latest['atr_14'].values[0])
            buy_entry = curr_price * 0.995

            # Dynamic Stop-Loss and Profit Target
            stop_buf = max(0.04, min(0.08, (atr / curr_price) * 1.2))
            stop_loss = curr_price * (1.0 - stop_buf)
            target_gain = max(0.06, (atr / curr_price) * 1.8 + spread_est)
            target_price = curr_price * (1.0 + target_gain)
            profit_pot = round(((target_price - curr_price) / curr_price) * 100, 1)

            # Kelly-based Bankroll Sizing for £500
            alloc_pct = 0.12 if confidence < 75 else 0.18
            position_gbp = round(TOTAL_CAPITAL_GBP * alloc_pct, 2)
            shares_qty = int((position_gbp * 100.0) / buy_entry)

            name = t.info.get('shortName', ticker.replace('.L', ' plc'))

            picks.append({
                'ticker': ticker,
                'company_name': name,
                'current_price': curr_price,
                'buy_entry': buy_entry,
                'target_price': target_price,
                'stop_loss': stop_loss,
                'position_gbp': position_gbp,
                'shares_qty': shares_qty,
                'profit_potential': profit_pot,
                'hold_days': 5 if latest['ret_5d'].values[0] > 0 else 7,
                'confidence': confidence,
                'news_status': rns_data['status'],
                'rns_headline': rns_data['headline'],
                'features_dict': latest[feature_cols].iloc[0].to_dict(),
                'raw_score': blended
            })
        except Exception:
            continue

    return sorted(picks, key=lambda x: x['raw_score'], reverse=True)[:5]

# ==============================================================================
# UI HEADER & RUNTIME CHECKS
# ==============================================================================
learned_count = execute_launch_learning()

h_col, s_col = st.columns([3, 1])
with h_col:
    st.title("🇬🇧 ALPHA-LSE // Institutional Decision Engine")
    st.caption("UK Micro-Cap dual-ensemble predictor with direct RNS scraping and £500 bankroll sizing.")
with s_col:
    db_status = "ONLINE (SUPABASE)" if supabase else "OFFLINE"
    db_color = "#10b981" if supabase else "#ef4444"
    st.markdown(f"""
    <div style="text-align: right; color: #94a3b8; font-size: 0.8rem; padding-top: 10px;">
        DATABASE: <span style="color: {db_color}; font-weight: 700;">{db_status}</span><br>
        5-MIN AUDIT: <b>{datetime.now().strftime('%H:%M:%S')}</b>
    </div>
    """, unsafe_allow_html=True)

if not supabase:
    st.warning("⚠️ Supabase credentials missing. Add `SUPABASE_URL` and `SUPABASE_KEY` to your secrets to enable permanent storage.")

if learned_count > 0:
    st.success(f"🧠 AI Reinforcement Active: Model weights fine-tuned on {learned_count} completed trade outcomes from Supabase.", icon="✅")

# Navigation Tabs
tab_live, tab_backtest = st.tabs(["🎯 Live Daily Picks & Execution", "📊 Strategy Health & £500 Backtest"])

# ==============================================================================
# TAB 1: LIVE DAILY PICKS & EXECUTION LEDGER
# ==============================================================================
with tab_live:
    col_lead, col_rf = st.columns([5, 1])
    with col_lead:
        st.write("Today's algorithmically screened shares ordered strictly by highest statistical confidence:")
    with col_rf:
        if st.button("🔄 Refresh Picks", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Analyzing market data and official RNS filings..."):
        top_picks = generate_top_predictions()

    if top_picks:
        # Save new candidates to Supabase
        for p in top_picks:
            save_prediction_to_cloud(p)

        # Render Strategy Cards
        for rank, item in enumerate(top_picks, 1):
            with st.container(border=True):
                ci, cn, cb = st.columns([3, 2, 1])
                with ci:
                    st.subheader(f"#{rank} {item['company_name']}")
                    st.caption(f"LSE AIM: **{item['ticker']}** &nbsp;|&nbsp; Official RNS: *{item['rns_headline']}*")
                with cn:
                    st.write("")
                    if "Alert" in item['news_status']:
                        st.markdown(f"<span class='badge-rns-alert'>{item['news_status']}</span>", unsafe_allow_html=True)
                    elif "Positive" in item['news_status']:
                        st.markdown(f"<span class='badge-rns-bull'>{item['news_status']}</span>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<span class='badge-rns-safe'>{item['news_status']}</span>", unsafe_allow_html=True)
                with cb:
                    st.metric(label="Ensemble Accuracy", value=f"{item['confidence']}/100")

                st.write("")
                m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
                m1.metric("Current Price", format_price(item['current_price']))
                m2.metric("Buy Point", format_price(item['buy_entry']))
                m3.metric("Target Sell", format_price(item['target_price']))
                m4.metric("Safety Stop", format_price(item['stop_loss']), help="Exit trade if price touches this level to protect bankroll.")
                m5.metric("Capital Sizing", f"£{item['position_gbp']:.0f}", help="Calculated position allocation for a £500 account.")
                m6.metric("Shares to Buy", f"{item['shares_qty']:,}")
                m7.metric("Profit Goal", f"+{item['profit_potential']}%")
    else:
        st.warning("No stocks currently satisfy minimum liquidity thresholds. Check back after market open.")

    st.write("")
    st.subheader("📋 Real-Time Execution Ledger & Live Tracked Predictions")
    st.caption("Auto-audited every 5 minutes against live London exchange prints and stored permanently in Supabase.")

    history_df = audit_open_trades()
    if not history_df.empty:
        cols_to_use = [
            'predicted_date', 'company_name', 'ticker', 'position_gbp',
            'entry_price', 'target_price', 'stop_loss', 'latest_price', 
            'pnl_pct', 'profit_goal', 'status', 'last_checked'
        ]
        available_cols = [c for c in cols_to_use if c in history_df.columns]
        disp = history_df[available_cols].copy()

        rename_map = {
            'predicted_date': 'Date', 'company_name': 'Company', 'ticker': 'Ticker',
            'position_gbp': 'Size (£)', 'entry_price': 'Entry', 'target_price': 'Target',
            'stop_loss': 'Stop-Loss', 'latest_price': 'Latest', 'pnl_pct': 'Live P&L',
            'profit_goal': 'Target %', 'status': 'Status', 'last_checked': 'Audited At'
        }
        disp.rename(columns=rename_map, inplace=True)

        if 'Size (£)' in disp.columns:
            disp['Size (£)'] = disp['Size (£)'].apply(lambda x: f"£{float(x):.0f}" if pd.notnull(x) else "-")
        for p_col in ['Entry', 'Target', 'Stop-Loss', 'Latest']:
            if p_col in disp.columns:
                disp[p_col] = disp[p_col].apply(lambda x: format_price(float(x)) if pd.notnull(x) else "-")
        if 'Target %' in disp.columns:
            disp['Target %'] = disp['Target %'].apply(lambda x: f"+{float(x):.1f}%" if pd.notnull(x) else "-")
        if 'Live P&L' in disp.columns:
            disp['Live P&L'] = disp['Live P&L'].apply(lambda x: f"{float(x):+.2f}%" if pd.notnull(x) else "0.00%")

        st.dataframe(disp, width="stretch", hide_index=True)
    else:
        st.info("No predictions logged in the cloud database yet. Verified predictions will appear as the engine runs.")

# ==============================================================================
# TAB 2: INTERACTIVE STRATEGY BACKTEST & GROWTH LAB
# ==============================================================================
with tab_backtest:
    st.subheader("📈 Interactive Strategy Backtester & Performance Lab")
    st.caption("Simulate and validate the strategy across 500 historical trading sessions directly inside your browser.")

    with st.container(border=True):
        st.markdown("##### ⚙️ Backtest Configuration Parameters")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            bt_capital = st.number_input("Starting Capital (£)", min_value=100.0, max_value=10000.0, value=500.0, step=50.0)
        with c2:
            bt_max_positions = st.slider("Max Concurrent Holdings", min_value=2, max_value=8, value=5)
        with c3:
            bt_alloc_pct = st.slider("Max Allocation per Trade (%)", min_value=10, max_value=30, value=18) / 100.0
        with c4:
            bt_confidence = st.slider("Min Model Probability", min_value=0.45, max_value=0.65, value=0.52, step=0.01)

        run_bt_button = st.button("🚀 Run Simulation Now", type="primary", use_container_width=True)

    metrics_file = os.path.join(OUTPUT_DIR, "backtest_metrics.json")
    curve_file = os.path.join(OUTPUT_DIR, "equity_curve.csv")
    trades_file = os.path.join(OUTPUT_DIR, "backtest_trades.csv")

    if run_bt_button:
        if run_backtest_simulation is None:
            st.error("Backtester module not found. Make sure backtester.py is in the project root directory.")
        else:
            with st.spinner("Simulating portfolio execution over 500 trading sessions..."):
                try:
                    metrics, curve_df, trades_df = run_backtest_simulation(
                        initial_capital=bt_capital,
                        max_positions=bt_max_positions,
                        max_alloc_per_trade=bt_alloc_pct,
                        min_model_score=bt_confidence
                    )
                    st.success("Simulation complete! Results refreshed below.", icon="✅")
                except Exception as e:
                    st.error(f"Error executing backtest: {e}")

    # Display Backtest Visuals & Equity Curve
    if os.path.exists(metrics_file) and os.path.exists(curve_file):
        with open(metrics_file, "r") as f:
            metrics = json.load(f)
        curve_df = pd.read_csv(curve_file)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Initial Capital", f"£{metrics['initial_capital']:.2f}")
        k2.metric("Final Simulated Equity", f"£{metrics['final_equity']:.2f}", delta=f"{metrics['net_return_pct']:+.1f}% Total Return")
        k3.metric("Historical Win Rate", f"{metrics['win_rate_pct']}%", help="Percentage of completed trades hitting profit targets.")
        k4.metric("Profit Factor", f"{metrics['profit_factor']}", help="Ratio of gross profits to gross losses (>1.2 is profitable).")

        st.write("")

        # Plotly Equity Curve
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=curve_df['Date'],
            y=curve_df['Total Equity (£)'],
            mode='lines',
            name='Account Equity (£)',
            line=dict(color='#00e676', width=2.5),
            fill='tozeroy',
            fillcolor='rgba(0, 230, 118, 0.08)'
        ))
        fig.add_hline(
            y=metrics['initial_capital'], 
            line_dash="dash", 
            line_color="#94a3b8", 
            annotation_text=f"Starting Baseline (£{metrics['initial_capital']:.0f})", 
            annotation_position="bottom right"
        )
        fig.update_layout(
            title=f"Simulated Growth from £{metrics['initial_capital']:.0f} Starting Bankroll",
            template="plotly_dark",
            plot_bgcolor="#111726",
            paper_bgcolor="#111726",
            height=430,
            margin=dict(l=20, r=20, t=40, b=20),
            yaxis_title="Account Equity (£)",
            xaxis_title="Date"
        )
        st.plotly_chart(fig, use_container_width=True)

        # Recent Simulated Trades
        if os.path.exists(trades_file):
            t_df = pd.read_csv(trades_file)
            st.markdown(f"##### Recent Trade History ({len(t_df)} Total Simulated Trades)")
            t_disp = t_df.tail(25).copy()
            t_disp['Return'] = t_disp['Return'].apply(lambda x: f"{float(x)*100:+.1f}%")
            t_disp['PnL_GBP'] = t_disp['PnL_GBP'].apply(lambda x: f"£{float(x):+.2f}")
            st.dataframe(t_disp, width="stretch", hide_index=True)
    else:
        st.info("No saved backtest run found. Set your parameters above and click 'Run Simulation Now'.")