"""
Historical Backtest & Autonomous Autopsy Simulator.
Simulates past trading performance, catches failures, and teaches the AI self-correction.
"""

import os
import sys
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.self_learner import log_feature_vector_snapshot, execute_autonomous_autopsy

def run_backtest_simulation():
    print("=== STARTING HISTORICAL BACKTEST & AUTONOMOUS REINFORCEMENT ===")
    con = duckdb.connect("market_data.duckdb")
    
    symbols = con.execute("SELECT DISTINCT symbol FROM daily_candles WHERE symbol NOT LIKE '^%' LIMIT 10").df()["symbol"].tolist()
    
    total_simulated = 0
    total_wins = 0
    total_losses = 0

    for sym in symbols:
        df = con.execute(f"SELECT date, open, high, low, close, volume FROM daily_candles WHERE UPPER(symbol) = UPPER('{sym}') ORDER BY date ASC").df()
        if len(df) < 100:
            continue
            
        # Calculate features for simulation
        df["ema20"] = df["close"].ewm(span=20).mean()
        df["ema50"] = df["close"].ewm(span=50).mean()
        df["dist_ema20_pct"] = ((df["close"] - df["ema20"]) / df["ema20"]) * 100
        df["trend_spread_pct"] = ((df["ema20"] - df["ema50"]) / df["ema50"]) * 100
        df["atr_14"] = (df["high"] - df["low"]).rolling(14).mean()
        df["atr_pct"] = (df["atr_14"] / df["close"]) * 100
        df["rvol"] = df["volume"] / df["volume"].rolling(20).mean()
        
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df["rsi_14"] = 100 - (100 / (1 + (gain / loss)))
        df = df.dropna()

        for i in range(50, len(df) - 15):
            row = df.iloc[i]
            entry = row["close"]
            atr = row["atr_14"]
            if pd.isna(atr) or atr <= 0:
                continue
                
            target = entry + (1.5 * atr)
            stop = entry - (1.1 * atr)
            
            future = df.iloc[i+1 : i+11]
            hit_target = (future["high"] >= target).any()
            hit_stop = (future["low"] <= stop).any()
            
            feat_dict = {
                "dist_ema20_pct": float(row["dist_ema20_pct"]),
                "trend_spread_pct": float(row["trend_spread_pct"]),
                "atr_pct": float(row["atr_pct"]),
                "rvol": float(row["rvol"]),
                "rsi_14": float(row["rsi_14"]),
                "deliv_shock": 1.0
            }
            
            pred_id = f"bt_{sym}_{i}"
            log_feature_vector_snapshot(pred_id, sym, feat_dict)
            
            total_simulated += 1
            if hit_target and not hit_stop:
                total_wins += 1
            elif hit_stop and not hit_target:
                total_losses += 1
                # Trigger Autonomous Autopsy on backtest loss!
                execute_autonomous_autopsy(pred_id, "BACKTEST_FALSE_BREAKOUT")

    con.close()
    print(f"\n✓ Backtest Simulation Complete.")
    print(f"Total Trades Evaluated: {total_simulated:,}")
    print(f"Wins: {total_wins:,} | Losses: {total_losses:,}")
    print(f"🧠 AI Self-Learner successfully autopsied all backtest failures and updated penalty weights.")

if __name__ == "__main__":
    run_backtest_simulation()