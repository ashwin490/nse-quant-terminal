"""
Self-Learning Autonomous Reinforcement Engine.
Logs feature vector snapshots of every prediction and conducts autopsies
on failures (both live and backtested) to dynamically penalize bad patterns.
"""

import os
from pathlib import Path
import duckdb
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(ROOT_DIR, "market_data.duckdb")

def init_self_learning_tables():
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS feature_snapshots (
            pred_id VARCHAR,
            symbol VARCHAR,
            dist_ema20_pct DOUBLE,
            trend_spread_pct DOUBLE,
            atr_pct DOUBLE,
            rvol DOUBLE,
            rsi_14 DOUBLE,
            deliv_shock DOUBLE,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_mistakes (
            mistake_id VARCHAR PRIMARY KEY,
            failure_reason VARCHAR,
            rsi_range VARCHAR,
            rvol_range VARCHAR,
            penalty_weight DOUBLE
        )
    """)
    con.close()

init_self_learning_tables()

def log_feature_vector_snapshot(pred_id: str, symbol: str, feat_dict: dict):
    con = duckdb.connect(DB_PATH)
    con.execute("""
        INSERT INTO feature_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, [
        pred_id, symbol,
        feat_dict.get("dist_ema20_pct", 0.0),
        feat_dict.get("trend_spread_pct", 0.0),
        feat_dict.get("atr_pct", 0.0),
        feat_dict.get("rvol", 1.0),
        feat_dict.get("rsi_14", 50.0),
        feat_dict.get("deliv_shock", 1.0)
    ])
    con.close()

def execute_autonomous_autopsy(pred_id: str, failure_reason: str):
    """
    Performs an autopsy on a failed prediction (live or backtest),
    retrieves its feature snapshot, and logs a dynamic penalty rule.
    """
    con = duckdb.connect(DB_PATH)
    try:
        snap = con.execute("SELECT * FROM feature_snapshots WHERE pred_id = ?", [pred_id]).df()
        if not snap.empty:
            rsi = snap.iloc[0]["rsi_14"]
            rvol = snap.iloc[0]["rvol"]
            
            rsi_bucket = "HIGH_RSI" if rsi > 65 else ("LOW_RSI" if rsi < 40 else "MID_RSI")
            rvol_bucket = "LOW_VOL" if rvol < 1.0 else "HIGH_VOL"
            mistake_id = f"{failure_reason}_{rsi_bucket}_{rvol_bucket}"
            
            con.execute("""
                INSERT OR REPLACE INTO trade_mistakes VALUES (?, ?, ?, ?, 0.85)
            """, [mistake_id, failure_reason, rsi_bucket, rvol_bucket])
    except Exception as e:
        print(f"Autopsy execution error: {e}")
    finally:
        con.close()

# Alias function to satisfy auditor.py import requirements
def perform_trade_autopsy(pred_id: str, failure_reason: str):
    return execute_autonomous_autopsy(pred_id, failure_reason)

def get_mistake_penalty(feat_dict: dict) -> float:
    """
    Evaluates current feature vector against past mistakes.
    Returns a penalty multiplier (< 1.0) if matching a known failure pattern.
    """
    rsi = feat_dict.get("rsi_14", 50.0)
    rvol = feat_dict.get("rvol", 1.0)
    
    rsi_bucket = "HIGH_RSI" if rsi > 65 else ("LOW_RSI" if rsi < 40 else "MID_RSI")
    rvol_bucket = "LOW_VOL" if rvol < 1.0 else "HIGH_VOL"
    
    con = duckdb.connect(DB_PATH)
    try:
        res = con.execute("""
            SELECT penalty_weight FROM trade_mistakes 
            WHERE rsi_range = ? AND rvol_range = ?
        """, [rsi_bucket, rvol_bucket]).df()
        
        if not res.empty:
            return float(res.iloc[0]["penalty_weight"])
    except Exception:
        pass
    finally:
        con.close()
        
    return 1.0