import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import duckdb
import pandas as pd
import numpy as np
import joblib
import lightgbm as lgb

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.features import FEATURE_COLS_V2
from core.self_learner import perform_trade_autopsy

DB_PATH = "market_data.duckdb"
MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")

def run_weekend_retraining_maintenance():
    print("==================================================")
    print(f"🔧 Starting Quantitative Weekly Retraining: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")

    if not os.path.exists(MODEL_PATH):
        print("❌ Model artifact missing. Run baseline training first.")
        return

    con = duckdb.connect(DB_PATH)
    
    # Verify tables exist
    con.execute("""
        CREATE TABLE IF NOT EXISTS retrain_audit_log (
            run_date TIMESTAMP,
            trades_evaluated INTEGER,
            new_mistakes_logged INTEGER,
            status VARCHAR
        )
    """)

    # Pull closed trades recorded in audit ledger
    closed_trades = con.execute("""
        SELECT symbol, entry_price, exit_price, pnl_pct, status, target_price, stop_loss_price, logged_at 
        FROM trade_audit 
        WHERE status IN ('TARGET_HIT', 'STOPPED_OUT')
    """).fetchdf()

    if closed_trades.empty:
        print("ℹ️ No newly closed trades found in live audit ledger. Skipping model update.")
        con.execute("INSERT INTO retrain_audit_log VALUES (NOW(), 0, 0, 'SKIPPED_EMPTY')")
        con.close()
        return

    print(f"📊 Found {len(closed_trades)} completed trades in live ledger.")
    
    # Audit invalidations and feed forensic autopsy engine
    mistakes_added = 0
    training_samples = []

    for _, tr in closed_trades.iterrows():
        pnl = float(tr["pnl_pct"])
        sym = tr["symbol"]
        pred_id = f"{sym}_{pd.to_datetime(tr['logged_at']).strftime('%Y%m%d')}"

        # If trade hit stop, conduct forensic autopsy
        if tr["status"] == "STOPPED_OUT":
            diag = perform_trade_autopsy(sym, pred_id, pnl)
            mistakes_added += 1

        # Fetch recorded feature snapshot from DuckDB
        feat_row = con.execute("SELECT * FROM feature_snapshot WHERE prediction_id = ?", [pred_id]).fetchdf()
        if not feat_row.empty:
            r = feat_row.iloc[0]
            training_samples.append({
                "features": [
                    float(r.get("dist_ema20_pct", 0.0)),
                    float(r.get("trend_spread_pct", 0.0)),
                    float(r.get("atr_pct", 0.0)),
                    float(r.get("rvol", 1.0)),
                    float(r.get("rsi_14", 50.0)),
                    float(r.get("deliv_shock", 1.0))
                ],
                "label": 1 if tr["status"] == "TARGET_HIT" else 0,
                "weight": 1.0 if tr["status"] == "TARGET_HIT" else 2.5
            })

    # Batch Refit LightGBM Model
    if len(training_samples) >= 10:
        print(f"⚡ Batch refitting model on {len(training_samples)} real execution samples...")
        ml_model = joblib.load(MODEL_PATH)

        X = pd.DataFrame([s["features"] for s in training_samples], columns=FEATURE_COLS_V2[:6])
        y = np.array([s["label"] for s in training_samples])
        weights = np.array([s["weight"] for s in training_samples])

        ml_model.fit(X, y, sample_weight=weights)
        joblib.dump(ml_model, MODEL_PATH)
        print("✅ LightGBM model weights successfully calibrated.")
        status = "SUCCESS_RETRAINED"
    else:
        print(f"ℹ️ {len(training_samples)} samples collected (Hurdle: 10 required for batch refit).")
        status = "ACCUMULATING"

    con.execute("""
        INSERT INTO retrain_audit_log VALUES (NOW(), ?, ?, ?)
    """, [len(closed_trades), mistakes_added, status])
    con.close()

    print(f"🎉 Maintenance Complete! Logged {mistakes_added} new autopsies.\n")

if __name__ == "__main__":
    run_weekend_retraining_maintenance()