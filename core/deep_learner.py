import os
import sys
from pathlib import Path
from datetime import datetime
import duckdb
import pandas as pd
import numpy as np
import joblib
import yfinance as yf
import lightgbm as lgb

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.data_engine import NIFTY_BASKET
from core.universe_sync import NIFTY_200_UNIVERSE

DB_PATH = "market_data.duckdb"
MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")
CHECKPOINT_TABLE = "deep_learning_checkpoint"

FEATURE_COLS = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock"
]

def init_deep_learning_schema():
    con = duckdb.connect(DB_PATH)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
            id INTEGER PRIMARY KEY,
            last_processed_date VARCHAR,
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            mistakes_learned INTEGER,
            cumulative_pnl DOUBLE,
            updated_at TIMESTAMP
        )
    """)
    con.close()

def calculate_rolling_features(df_sym: pd.DataFrame) -> pd.DataFrame:
    if len(df_sym) < 60:
        return pd.DataFrame()

    df = df_sym.copy().sort_values("date").reset_index(drop=True)

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    df["dist_ema20_pct"] = ((df["close"] - df["ema20"]) / df["ema20"]) * 100.0
    df["trend_spread_pct"] = ((df["ema20"] - df["ema50"]) / df["ema50"]) * 100.0

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean().bfill()
    df["atr_pct"] = (df["atr_14"] / df["close"]) * 100.0

    vol_sma20 = df["volume"].rolling(20).mean().replace(0, np.nan)
    df["rvol"] = (df["volume"] / vol_sma20).fillna(1.0)

    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    df["deliv_shock"] = (df["volume"] / df["volume"].rolling(10).mean()).clip(0.5, 3.0).fillna(1.0)
    df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df

def run_deep_learning_loop():
    init_deep_learning_schema()

    con = duckdb.connect(DB_PATH)
    con.execute(f"DELETE FROM {CHECKPOINT_TABLE}")
    
    df_raw = con.execute("""
        SELECT symbol, date, open, high, low, close, volume 
        FROM daily_candles 
        WHERE date >= '2006-01-01'
        ORDER BY symbol, date ASC
    """).fetchdf()
    con.close()

    if df_raw.empty:
        print("❌ No candle data found in DuckDB.")
        return

    print("⚙️ Computing factor matrices across universe...")
    processed_symbols = {}
    for sym, grp in df_raw.groupby("symbol"):
        f_df = calculate_rolling_features(grp)
        if not f_df.empty:
            processed_symbols[sym] = f_df.set_index("date_str")

    all_dates = sorted(list(set(
        pd.to_datetime(df_raw["date"]).dt.strftime("%Y-%m-%d")
    )))

    warmup_days = 120
    active_dates = all_dates[warmup_days:]

    total_trades = 0
    wins = 0
    losses = 0
    mistakes_learned = 0
    cumulative_pnl = 0.0

    training_memory = []
    
    ml_model = lgb.LGBMClassifier(
        n_estimators=100,
        learning_rate=0.03,
        num_leaves=20,
        min_child_samples=25,
        class_weight="balanced",
        random_state=42,
        verbose=-1
    )

    mistake_memory = {
        "MOMENTUM_EXHAUSTION": 0,
        "SPECULATIVE_FAKEOUT": 0,
        "MEAN_REVERSION_WHIPSAW": 0,
        "VOLUME_DRYUP": 0
    }

    active_trades = []

    print(f"\n🧠 Commencing Walk-Forward Engine across {len(active_dates)} sessions...")
    print("Rules: Max 12 Days Holding | Target +1.5 ATR | Stop -1.0 ATR | Breakeven +0.8 ATR")
    print("Refit every 65 sessions (~3 months) using validated outcomes.\n")

    try:
        for idx, curr_date_str in enumerate(active_dates):

            # --- 1. PERIODIC RE-TRAINING (Every 65 Sessions / ~3 Months) ---
            if idx > 0 and idx % 65 == 0 and len(training_memory) >= 40:
                df_mem = pd.DataFrame(training_memory)
                X_train = pd.DataFrame(list(df_mem["features"]), columns=FEATURE_COLS)
                y_train = df_mem["label"].values

                ml_model.fit(X_train, y_train)
                print(f"  ⚡ [Batch Refit] Re-calibrated on {len(df_mem)} trade outcomes.")

            # --- 2. MANAGE ACTIVE POSITIONS ---
            still_active = []
            for trade in active_trades:
                sym = trade["symbol"]
                if sym not in processed_symbols or curr_date_str not in processed_symbols[sym].index:
                    # If symbol delisted or missing data, close position
                    trade["days_held"] += 1
                    if trade["days_held"] > 10:
                        continue
                    still_active.append(trade)
                    continue

                bar = processed_symbols[sym].loc[curr_date_str]
                high = float(bar["high"])
                low = float(bar["low"])
                close = float(bar["close"])

                trade["days_held"] += 1
                trade["highest"] = max(trade["highest"], high)
                gain = trade["highest"] - trade["entry"]
                atr = trade["atr"]

                # Ratchet: Breakeven once gain reaches +0.8 ATR
                if gain >= (0.8 * atr):
                    trade["stop"] = max(trade["stop"], trade["entry"] * 1.002)

                # TARGET HIT (+1.5 ATR)
                if high >= trade["target"]:
                    net_gain = (trade["target"] - trade["entry"]) * trade["qty"] - (trade["entry"] * trade["qty"] * 0.0015)
                    cumulative_pnl += net_gain
                    wins += 1
                    total_trades += 1

                    training_memory.append({
                        "features": trade["features"],
                        "label": 1
                    })

                # STOP LOSS HIT
                elif low <= trade["stop"]:
                    exit_price = trade["stop"]
                    net_loss = (exit_price - trade["entry"]) * trade["qty"] - (trade["entry"] * trade["qty"] * 0.0015)
                    cumulative_pnl += net_loss
                    total_trades += 1

                    if exit_price >= trade["entry"]:
                        wins += 1  # Breakeven capital preservation
                    else:
                        losses += 1
                        snap = trade["features"]
                        
                        if snap["rsi_14"] > 66.0:
                            cause = "MOMENTUM_EXHAUSTION"
                        elif snap["deliv_shock"] < 1.0:
                            cause = "SPECULATIVE_FAKEOUT"
                        elif snap["dist_ema20_pct"] > 3.8:
                            cause = "MEAN_REVERSION_WHIPSAW"
                        else:
                            cause = "VOLUME_DRYUP"

                        mistake_memory[cause] += 1
                        mistakes_learned += 1

                        training_memory.append({
                            "features": snap,
                            "label": 0
                        })

                # TIME-BASED EXIT: Max 12 Trading Days (~2.5 weeks)
                elif trade["days_held"] >= 12:
                    net_pnl = (close - trade["entry"]) * trade["qty"] - (trade["entry"] * trade["qty"] * 0.0015)
                    cumulative_pnl += net_pnl
                    total_trades += 1

                    if net_pnl > 0:
                        wins += 1
                        training_memory.append({"features": trade["features"], "label": 1})
                    else:
                        losses += 1
                        training_memory.append({"features": trade["features"], "label": 0})
                else:
                    still_active.append(trade)

            active_trades = still_active

            # --- 3. CANDIDATE SELECTION (TOP-K RELATIVE RANKING) ---
            slots_available = 4 - len(active_trades)
            if slots_available > 0:
                candidates = []
                for sym, sym_df in processed_symbols.items():
                    if curr_date_str not in sym_df.index:
                        continue

                    row = sym_df.loc[curr_date_str]
                    close = float(row["close"])
                    ema20 = float(row["ema20"])
                    ema50 = float(row["ema50"])
                    atr = float(row["atr_14"])

                    # Require baseline upward structure
                    if close < ema50:
                        continue

                    feat_dict = {
                        "dist_ema20_pct": float(row["dist_ema20_pct"]),
                        "trend_spread_pct": float(row["trend_spread_pct"]),
                        "atr_pct": float(row["atr_pct"]),
                        "rvol": float(row["rvol"]),
                        "rsi_14": float(row["rsi_14"]),
                        "deliv_shock": float(row["deliv_shock"])
                    }

                    # Filter out overextended entries
                    if feat_dict["rsi_14"] > 70.0 or feat_dict["dist_ema20_pct"] > 5.0:
                        continue

                    feat_vec = pd.DataFrame([feat_dict], columns=FEATURE_COLS)

                    if len(training_memory) >= 40:
                        try:
                            raw_prob = float(ml_model.predict_proba(feat_vec)[0][1] * 100.0)
                        except Exception:
                            raw_prob = 50.0
                    else:
                        raw_prob = 50.0
                        if 45.0 <= feat_dict["rsi_14"] <= 62.0:
                            raw_prob += 5.0
                        if feat_dict["rvol"] > 1.1:
                            raw_prob += 5.0

                    # Dynamic mistake penalty with exponential decay
                    penalty = 1.0
                    if mistake_memory["MOMENTUM_EXHAUSTION"] > 5 and feat_dict["rsi_14"] > 64.0:
                        penalty *= 0.85
                    if mistake_memory["SPECULATIVE_FAKEOUT"] > 5 and feat_dict["deliv_shock"] < 1.05:
                        penalty *= 0.85
                    if mistake_memory["MEAN_REVERSION_WHIPSAW"] > 5 and feat_dict["dist_ema20_pct"] > 3.2:
                        penalty *= 0.85

                    score = raw_prob * penalty

                    qty = 2 if close <= 500 else (3 if close <= 1000 else (4 if close <= 2000 else (2 if close <= 5000 else 1)))

                    candidates.append({
                        "symbol": sym,
                        "entry": close,
                        "target": round(close + (1.5 * atr), 2),
                        "stop": round(close - (1.0 * atr), 2),
                        "atr": atr,
                        "qty": qty,
                        "highest": close,
                        "score": score,
                        "days_held": 0,
                        "features": feat_dict
                    })

                # Relative Top-K Selection: Sort candidates by score and pick the best available
                candidates.sort(key=lambda x: x["score"], reverse=True)
                for cand in candidates[:slots_available]:
                    active_trades.append(cand)

            # --- 4. STATUS LOGGING (Every 50 sessions) ---
            if idx % 50 == 0 or idx == len(active_dates) - 1:
                wr = round((wins / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
                print(f"[{curr_date_str}] Trades: {total_trades:<5} | Win Rate: {wr}% ({wins}W / {losses}L) | Autopsies: {mistakes_learned:<4} | Net PnL: ₹{cumulative_pnl:,.0f}")

                os.makedirs(os.path.join(ROOT_DIR, "models"), exist_ok=True)
                joblib.dump(ml_model, MODEL_PATH)

                con = duckdb.connect(DB_PATH)
                con.execute(f"""
                    INSERT OR REPLACE INTO {CHECKPOINT_TABLE} 
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                """, [curr_date_str, total_trades, wins, losses, mistakes_learned, cumulative_pnl, datetime.now()])
                con.close()

    except KeyboardInterrupt:
        print("\n⏸️ Engine paused by user. Progress saved.")
        return

    print("\n🎉 Walk-Forward Learning Complete!")
    print(f"Calibrated Model Saved to: {MODEL_PATH}")

if __name__ == "__main__":
    run_deep_learning_loop()