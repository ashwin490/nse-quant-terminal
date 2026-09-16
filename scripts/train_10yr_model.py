"""
Universal 10-Year Ensemble Model Training Engine (LightGBM + XGBoost).
Dynamically queries DuckDB for ALL available market equities, extracts multi-factor features,
trains an ensemble of top-tier gradient boosters, and saves the combined model.
"""

import os
import sys
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np
import yfinance as yf
import joblib
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

MODEL_DIR = os.path.join(ROOT_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "lgbm_stock_ranker.pkl") # Kept filename compatible with app.py

def get_all_database_symbols() -> list:
    con = duckdb.connect("market_data.duckdb")
    try:
        query = "SELECT DISTINCT symbol FROM daily_candles WHERE symbol NOT LIKE '^%'"
        df = con.execute(query).df()
        symbols = df["symbol"].tolist()
    except Exception as e:
        print(f"Error querying symbols from DuckDB: {e}")
        symbols = []
    finally:
        con.close()
    return symbols

def train_ensemble_model():
    print("=== STARTING 10-YEAR ENSEMBLE MODEL TRAINING (LightGBM + XGBoost) ===")
    
    symbols = get_all_database_symbols()
    if not symbols:
        print("❌ Error: No symbols found in DuckDB.")
        return
        
    print(f"Discovered {len(symbols)} market equities. Extracting historical features...")
    
    con = duckdb.connect("market_data.duckdb")
    training_rows = []

    for sym in symbols:
        try:
            query = f"""
                SELECT date, open, high, low, close, volume 
                FROM daily_candles 
                WHERE UPPER(symbol) = UPPER('{sym}')
                ORDER BY date ASC
            """
            df = con.execute(query).df()
            
            if len(df) < 200:
                yf_sym = f"{sym}.NS"
                temp_df = yf.download(yf_sym, period="10y", interval="1d", progress=False)
                if isinstance(temp_df.columns, pd.MultiIndex):
                    temp_df.columns = [c[0] for c in temp_df.columns]
                if not temp_df.empty and "Close" in temp_df.columns:
                    temp_df = temp_df.reset_index()
                    temp_df.columns = [str(c).lower() for c in temp_df.columns]
                    rename_map = {}
                    for col in temp_df.columns:
                        if 'open' in col: rename_map[col] = 'open'
                        elif 'high' in col: rename_map[col] = 'high'
                        elif 'low' in col: rename_map[col] = 'low'
                        elif 'close' in col: rename_map[col] = 'close'
                        elif 'volume' in col: rename_map[col] = 'volume'
                        elif 'date' in col or 'index' in col: rename_map[col] = 'date'
                    temp_df = temp_df.rename(columns=rename_map)
                    df = temp_df[["date", "open", "high", "low", "close", "volume"]].dropna()

            if df.empty or len(df) < 200:
                continue

            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            # Calculate Technical Features
            df["ema20"] = df["close"].ewm(span=20).mean()
            df["ema50"] = df["close"].ewm(span=50).mean()
            df["dist_ema20_pct"] = ((df["close"] - df["ema20"]) / df["ema20"]) * 100
            df["trend_spread_pct"] = ((df["ema20"] - df["ema50"]) / df["ema50"]) * 100
            
            # ATR 14
            high_low = df["high"] - df["low"]
            high_close = np.abs(df["high"] - df["close"].shift())
            low_close = np.abs(df["low"] - df["close"].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["atr_14"] = tr.rolling(14).mean()
            df["atr_pct"] = (df["atr_14"] / df["close"]) * 100
            
            # RVOL
            df["vol_sma20"] = df["volume"].rolling(20).mean()
            df["rvol"] = df["volume"] / df["vol_sma20"]
            
            # RSI 14
            delta = df["close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df["rsi_14"] = 100 - (100 / (1 + rs))
            
            df = df.dropna()
            
            for i in range(len(df) - 15):
                row = df.iloc[i]
                entry_price = row["close"]
                atr = row["atr_14"]
                if pd.isna(atr) or atr <= 0:
                    continue
                target = entry_price + (1.5 * atr)
                stop = entry_price - (1.1 * atr)
                
                future_window = df.iloc[i+1 : i+11]
                hit_target = (future_window["high"] >= target).any()
                hit_stop = (future_window["low"] <= stop).any()
                
                if hit_target and not hit_stop:
                    label = 1
                elif hit_stop and not hit_target:
                    label = 0
                else:
                    continue
                    
                training_rows.append({
                    "dist_ema20_pct": float(row["dist_ema20_pct"]),
                    "trend_spread_pct": float(row["trend_spread_pct"]),
                    "atr_pct": float(row["atr_pct"]),
                    "rvol": float(row["rvol"]),
                    "rsi_14": float(row["rsi_14"]),
                    "deliv_shock": 1.0,
                    "target": label
                })
        except Exception:
            continue

    con.close()

    if not training_rows:
        print("❌ Error: Insufficient training samples.")
        return

    df_train = pd.DataFrame(training_rows)
    print(f"✓ Generated {len(df_train)} training samples for ensemble.")
    
    feature_cols = ["dist_ema20_pct", "trend_spread_pct", "atr_pct", "rvol", "rsi_14", "deliv_shock"]
    X = df_train[feature_cols]
    y = df_train["target"]
    
    # Train LightGBM
    print("Training LightGBM component...")
    lgb_model = LGBMClassifier(n_estimators=150, learning_rate=0.03, max_depth=5, random_state=42)
    lgb_model.fit(X, y)
    
    # Train XGBoost
    print("Training XGBoost component...")
    xgb_model = XGBClassifier(n_estimators=150, learning_rate=0.03, max_depth=5, random_state=42, eval_metric="logloss")
    xgb_model.fit(X, y)
    
    # Package both models into a dictionary tuple for ensemble voting
    ensemble_package = {
        "lgb": lgb_model,
        "xgb": xgb_model,
        "type": "ensemble"
    }
    
    joblib.dump(ensemble_package, MODEL_PATH)
    print(f"✓ Ensemble Model (LightGBM + XGBoost) successfully saved to {MODEL_PATH}")

if __name__ == "__main__":
    train_ensemble_model()