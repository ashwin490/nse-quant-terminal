import os
import sys
import time
from pathlib import Path
from datetime import datetime
from core.sector_map import apply_sector_concentration_cap, get_stock_sector

# Resolve project root for headless background execution
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import joblib
import pandas as pd
from plyer import notification

from core.data_engine import NIFTY_BASKET, sync_symbol_history
from core.features import extract_features
from core.regime import get_market_regime
from core.auditor import log_predictions
from core.delivery import fetch_delivery_metrics
from core.announcements import check_corporate_announcements
from core.risk_engine import calculate_position_size
from core.telegram_bot import dispatch_batch_alerts

MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")
FEATURE_COLS = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock"
]

# Baseline portfolio configuration for automated runs
DEFAULT_PORTFOLIO_EQUITY = 500000.0  # ₹5 Lakhs
DEFAULT_RISK_PER_TRADE_PCT = 1.5     # 1.5% max risk cap

def trigger_desktop_notification(top_trades: list, regime: str):
    """Fires a native Windows desktop toast alert."""
    if not top_trades:
        notification.notify(
            title="NSE Pre-Market AI Scanner",
            message="Scan finished: No setups satisfied risk-reward criteria today.",
            app_name="NSE Quant Terminal",
            timeout=10
        )
        return

    lines = [f"Market Regime: {regime}"]
    for rank, trade in enumerate(top_trades, 1):
        lines.append(
            f"#{rank} {trade['Ticker']} | Prob: {trade['ML Probability']} | "
            f"Buy: {trade['Qty to Buy']} sh (₹{trade['Capital (₹)']:,}) | Tgt: ₹{trade['Target']}"
        )

    body = "\n".join(lines)

    notification.notify(
        title="🔔 Top Sized NSE Pre-Market Setups",
        message=body,
        app_name="NSE Quant Terminal",
        timeout=15
    )

def run_headless_scan() -> list:
    """Executes the full pipeline without launching the UI."""
    if not os.path.exists(MODEL_PATH):
        print("[ERROR] Model file not found. Train via 'python -m models.trainer' first.")
        return []

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Initializing 9:08 AM Ingestion & Tiered Sizing Engine...")
    ml_model = joblib.load(MODEL_PATH)
    macro = get_market_regime()

    results = []
    for symbol in NIFTY_BASKET:
        clean_sym = symbol.replace(".NS", "")
        sync_symbol_history(symbol, lookback_days=120)

        deliv_info = fetch_delivery_metrics(clean_sym)
        deliv_pct = deliv_info.get("deliv_pct", 35.0)
        deliv_shock = deliv_info.get("deliv_shock", 1.0)

        event_info = check_corporate_announcements(clean_sym)

        df_feat = extract_features(clean_sym, deliv_shock=deliv_shock)
        if df_feat.empty:
            continue

        latest = df_feat.iloc[-1]
        feat_vec = pd.DataFrame([[
            float(latest["dist_ema20_pct"]),
            float(latest["trend_spread_pct"]),
            float(latest["atr_pct"]),
            float(latest["rvol"]),
            float(latest["rsi_14"]),
            float(latest["deliv_shock"])
        ]], columns=FEATURE_COLS)

        raw_prob = float(ml_model.predict_proba(feat_vec)[0][1] * 100.0)
        final_score = raw_prob * macro["bias_multiplier"] * event_info["risk_penalty"]

        price = float(latest["close"])
        atr = float(latest["atr_14"])
        target = round(price + (1.5 * atr), 2)
        stop = round(price - (1.0 * atr), 2)

        # Position allocation using tiered price bracket logic
        sizing = calculate_position_size(
            account_size=DEFAULT_PORTFOLIO_EQUITY,
            max_risk_per_trade_pct=DEFAULT_RISK_PER_TRADE_PCT,
            entry_price=price,
            stop_loss_price=stop,
            win_prob_pct=raw_prob,
            payoff_ratio=1.5,
            kelly_fraction=0.5,
            use_tiered_brackets=True
        )

        results.append({
            "Ticker": clean_sym,
            "Price": round(price, 2),
            "ATR": round(atr, 2),
            "ML Probability": f"{round(raw_prob, 1)}%",
            "Adjusted Score": round(final_score, 1),
            "Filing Status": event_info["status"],
            "Delivery %": f"{deliv_pct}%",
            "Deliv Shock": f"{deliv_shock}",
            "Qty to Buy": sizing["shares"],
            "Capital (₹)": sizing["capital_allocated"],
            "Max Risk (₹)": sizing["actual_risk_rupees"],
            "Port Weight": f"{sizing['portfolio_weight_pct']}%",
            "Target": target,
            "Stop Loss": stop,
            "Latest Headline": event_info["headline"]
        })

    if not results:
        return []

    df_res = pd.DataFrame(results).sort_values("Adjusted Score", ascending=False).reset_index(drop=True)
    
    # Auto-log Top 5 to DuckDB audit table
    log_predictions(df_res.head(5).rename(columns={
        "Price": "Price (₹)",
        "Target": "Target (₹)",
        "Stop Loss": "Stop Loss (₹)"
    }))

    diversified_records = apply_sector_concentration_cap(df_res.to_dict(orient="records"), max_per_sector=2) 
    top_3 = diversified_records[:3]

    # 1. Desktop Notification
    trigger_desktop_notification(top_3, macro["regime"])

    # 2. Mobile Telegram Dispatch
    dispatch_batch_alerts(top_3, macro)
    
    return top_3

def start_daemon_clock():
    """
    Watches the system clock and triggers the scan at 09:08 AM IST 
    on Monday through Friday.
    """
    print("=== NSE Pre-Market Background Daemon Active ===")
    print("Armed for 09:08 AM IST (Mon-Fri) with Tiered Position Sizing & Telegram Alerts.")
    print("Press Ctrl + C to stop.\n")
    
    triggered_today = False

    while True:
        now = datetime.now()
        weekday = now.weekday()  # 0=Mon, 4=Fri

        if weekday < 5:
            if now.hour == 9 and now.minute == 8 and not triggered_today:
                print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Executing 9:08 AM Pre-Market Scan...")
                run_headless_scan()
                triggered_today = True

            if now.hour > 9 or (now.hour == 9 and now.minute > 15):
                triggered_today = False

        time.sleep(30)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NSE Pre-Market Alert Service")
    parser.add_argument("--now", action="store_true", help="Run scan immediately and trigger mobile alerts")
    args = parser.parse_args()

    if args.now:
        print("Executing on-demand scan with tiered sizing...")
        picks = run_headless_scan()
        print(f"Dispatched alerts for {len(picks)} setups.")
    else:
        start_daemon_clock()