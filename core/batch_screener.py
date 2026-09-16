import os
import sys
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np
import joblib

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.universe_sync import NIFTY_200_UNIVERSE, fetch_master_nse_delivery_bhavcopy
from core.features import extract_features
from core.regime import get_market_regime
from core.risk_engine import calculate_position_size
from core.announcements import check_corporate_announcements

MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")
FEATURE_COLS = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock"
]

def scan_nifty_200(total_capital: float = 500000, risk_pct: float = 1.5) -> pd.DataFrame:
    """
    High-throughput screener scoring the full Nifty 200 universe in memory.
    """
    if not os.path.exists(MODEL_PATH):
        print("Model file missing. Train first.")
        return pd.DataFrame()

    ml_model = joblib.load(MODEL_PATH)
    macro = get_market_regime()
    
    # 1. Grab delivery snapshot for universe in 1 call
    bhav = fetch_master_nse_delivery_bhavcopy()
    deliv_dict = {}
    if not bhav.empty:
        deliv_dict = bhav.set_index("SYMBOL")["DELIV_PER"].to_dict()

    feature_rows = []
    metadata = []

    print(f"Engineering feature vectors for {len(NIFTY_200_UNIVERSE)} universe instruments...")

    for sym in NIFTY_200_UNIVERSE:
        deliv_pct = float(deliv_dict.get(sym, 35.0))
        # Estimate shock based on delivery baseline
        deliv_shock = round(deliv_pct / 35.0, 2)

        df_feat = extract_features(sym, deliv_shock=deliv_shock)
        if df_feat.empty or len(df_feat) < 1:
            continue

        latest = df_feat.iloc[-1]
        
        feature_rows.append([
            float(latest["dist_ema20_pct"]),
            float(latest["trend_spread_pct"]),
            float(latest["atr_pct"]),
            float(latest["rvol"]),
            float(latest["rsi_14"]),
            float(latest["deliv_shock"])
        ])

        metadata.append({
            "Ticker": sym,
            "Price (₹)": round(float(latest["close"]), 2),
            "ATR": round(float(latest["atr_14"]), 2),
            "Delivery %": f"{deliv_pct}%",
            "Deliv Shock": f"{deliv_shock}x",
            "RSI (14)": round(float(latest["rsi_14"]), 1),
            "RVOL": round(float(latest["rvol"]), 2),
            "Target (₹)": round(float(latest["close"]) + (1.5 * float(latest["atr_14"])), 2),
            "Stop Loss (₹)": round(float(latest["close"]) - (1.0 * float(latest["atr_14"])), 2),
            "FullSymbol": f"{sym}.NS"
        })

    if not feature_rows:
        return pd.DataFrame()

    # 2. Vectorized batch matrix prediction (all 200 stocks evaluated in 1 CPU operation)
    X_matrix = pd.DataFrame(feature_rows, columns=FEATURE_COLS)
    probabilities = ml_model.predict_proba(X_matrix)[:, 1] * 100.0

    # 3. Assemble and rank results
    results = []
    for i, meta in enumerate(metadata):
        prob = round(float(probabilities[i]), 1)
        base_score = prob * macro["bias_multiplier"]

        meta["ML Probability"] = f"{prob}%"
        meta["RawScore"] = base_score
        results.append(meta)

    df_ranked = pd.DataFrame(results).sort_values("RawScore", ascending=False).reset_index(drop=True)

    # 4. Filter top 10 candidates for corporate announcement verification (prevents 200 web scrapings)
    final_top = []
    for idx, row in df_ranked.head(10).iterrows():
        event = check_corporate_announcements(row["Ticker"])
        adj_score = round(row["RawScore"] * event["risk_penalty"], 1)

        sizing = calculate_position_size(
            account_size=total_capital,
            max_risk_per_trade_pct=risk_pct,
            entry_price=row["Price (₹)"],
            stop_loss_price=row["Stop Loss (₹)"],
            win_prob_pct=float(row["ML Probability"].replace("%", "")),
            payoff_ratio=1.5,
            kelly_fraction=0.5
        )

        row_dict = row.to_dict()
        row_dict["Adjusted Score"] = adj_score
        row_dict["Filing Status"] = event["status"]
        row_dict["Latest Headline"] = event["headline"]
        row_dict["Qty to Buy"] = sizing["shares"]
        row_dict["Capital (₹)"] = sizing["capital_allocated"]
        row_dict["Max Risk (₹)"] = sizing["actual_risk_rupees"]
        row_dict["Port Weight"] = f"{sizing['portfolio_weight_pct']}%"
        final_top.append(row_dict)

    df_final = pd.DataFrame(final_top).sort_values("Adjusted Score", ascending=False).reset_index(drop=True)
    return df_final

if __name__ == "__main__":
    res = scan_nifty_200()
    print("\n=== TOP 5 NIFTY 200 INSTITUTIONAL PICKS ===")
    print(res[["Ticker", "Price (₹)", "ML Probability", "Adjusted Score", "Qty to Buy", "Filing Status"]].head(5).to_string(index=False))