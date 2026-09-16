import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb
import pandas as pd
import numpy as np
import joblib

DB_PATH = "market_data.duckdb"
MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")

FEATURE_COLS = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock"
]

def calculate_historical_features(df_sym: pd.DataFrame) -> pd.DataFrame:
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

def run_historical_backtest(
    start_date: str = "2023-01-01",
    initial_capital: float = 100000.0,
    slot_allocation: float = 25000.0
) -> dict:
    if not os.path.exists(MODEL_PATH):
        return {"error": "LightGBM model artifact not found. Train models first."}

    ml_model = joblib.load(MODEL_PATH)
    con = duckdb.connect(DB_PATH)

    df_raw = con.execute(f"""
        SELECT symbol, date, open, high, low, close, volume 
        FROM daily_candles 
        WHERE date >= '{start_date}' 
        ORDER BY symbol, date ASC
    """).fetchdf()
    con.close()

    if df_raw.empty:
        return {"error": "No candle data found in DuckDB."}

    processed_symbols = {}
    for sym, group in df_raw.groupby("symbol"):
        feat_df = calculate_historical_features(group)
        if not feat_df.empty:
            processed_symbols[sym] = feat_df.set_index("date_str")

    all_dates = sorted(list(set(pd.to_datetime(df_raw["date"]).dt.strftime("%Y-%m-%d"))))
    capital = initial_capital
    active_trades = []
    closed_trades = []
    equity_curve = []

    for curr_date in all_dates:
        # 1. Manage active positions
        still_active = []
        for trade in active_trades:
            sym = trade["symbol"]
            if sym not in processed_symbols or curr_date not in processed_symbols[sym].index:
                trade["days_held"] += 1
                if trade["days_held"] > 12:
                    continue
                still_active.append(trade)
                continue

            bar = processed_symbols[sym].loc[curr_date]
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])

            trade["days_held"] += 1
            trade["highest"] = max(trade["highest"], high)
            gain = trade["highest"] - trade["entry"]
            atr = trade["atr"]

            # Ratchet stops:
            # At +0.8 ATR gain, lock stop to Entry + 0.3% (guarantees profit covers fees)
            if gain >= (0.8 * atr):
                trade["stop"] = max(trade["stop"], trade["entry"] * 1.003)

            # At +1.2 ATR, trail 0.8 ATR below highest peak
            if gain >= (1.2 * atr):
                trade["stop"] = max(trade["stop"], trade["highest"] - (0.8 * atr))

            # TARGET HIT (+1.5 ATR)
            if high >= trade["target"]:
                gross_pnl = (trade["target"] - trade["entry"]) * trade["qty"]
                fees = (trade["entry"] * trade["qty"] * 0.0015)
                net_pnl = round(gross_pnl - fees, 2)

                capital += (trade["entry"] * trade["qty"]) + net_pnl
                trade["exit_price"] = trade["target"]
                trade["exit_date"] = curr_date
                trade["pnl_rupees"] = net_pnl
                trade["pnl_pct"] = round((net_pnl / (trade["entry"] * trade["qty"])) * 100.0, 2)
                trade["status"] = "TARGET_HIT"
                closed_trades.append(trade)

            # STOP LOSS HIT
            elif low <= trade["stop"]:
                exit_price = trade["stop"]
                gross_pnl = (exit_price - trade["entry"]) * trade["qty"]
                fees = (trade["entry"] * trade["qty"] * 0.0015)
                net_pnl = round(gross_pnl - fees, 2)

                capital += (trade["entry"] * trade["qty"]) + net_pnl
                trade["exit_price"] = exit_price
                trade["exit_date"] = curr_date
                trade["pnl_rupees"] = net_pnl
                trade["pnl_pct"] = round((net_pnl / (trade["entry"] * trade["qty"])) * 100.0, 2)
                trade["status"] = "STOPPED_OUT" if net_pnl < 0 else "BREAKEVEN_PROFIT"
                closed_trades.append(trade)

            # MAX DURATION EXIT (12 trading days)
            elif trade["days_held"] >= 12:
                gross_pnl = (close - trade["entry"]) * trade["qty"]
                fees = (trade["entry"] * trade["qty"] * 0.0015)
                net_pnl = round(gross_pnl - fees, 2)

                capital += (trade["entry"] * trade["qty"]) + net_pnl
                trade["exit_price"] = close
                trade["exit_date"] = curr_date
                trade["pnl_rupees"] = net_pnl
                trade["pnl_pct"] = round((net_pnl / (trade["entry"] * trade["qty"])) * 100.0, 2)
                trade["status"] = "TIME_EXPIRATION"
                closed_trades.append(trade)
            else:
                still_active.append(trade)

        active_trades = still_active

        # 2. Select candidates with Strict Quality Gate
        slots_available = 4 - len(active_trades)
        if slots_available > 0 and capital >= 10000.0:
            candidates = []
            for sym, sym_df in processed_symbols.items():
                if curr_date not in sym_df.index:
                    continue

                row = sym_df.loc[curr_date]
                close = float(row["close"])
                ema20 = float(row["ema20"])
                ema50 = float(row["ema50"])
                atr = float(row["atr_14"])

                # Structural filter: Price and short trend above 50-EMA
                if close < ema50 or ema20 < ema50:
                    continue

                feat_dict = {
                    "dist_ema20_pct": float(row["dist_ema20_pct"]),
                    "trend_spread_pct": float(row["trend_spread_pct"]),
                    "atr_pct": float(row["atr_pct"]),
                    "rvol": float(row["rvol"]),
                    "rsi_14": float(row["rsi_14"]),
                    "deliv_shock": float(row["deliv_shock"])
                }

                # Reject overbought exhaustion
                if feat_dict["rsi_14"] > 67.0 or feat_dict["dist_ema20_pct"] > 4.5 or feat_dict["rvol"] < 1.0:
                    continue

                feat_vec = pd.DataFrame([feat_dict], columns=FEATURE_COLS)
                try:
                    prob = float(ml_model.predict_proba(feat_vec)[0][1] * 100.0)
                except Exception:
                    prob = 50.0

                target_alloc = min(slot_allocation, capital)
                qty = max(1, int(target_alloc / close))

                candidates.append({
                    "symbol": sym,
                    "entry": close,
                    "entry_date": curr_date,
                    "target": round(close + (1.5 * atr), 2),
                    "stop": round(close - (1.1 * atr), 2),
                    "atr": atr,
                    "qty": qty,
                    "highest": close,
                    "score": prob,
                    "days_held": 0
                })

            candidates.sort(key=lambda x: x["score"], reverse=True)
            
            # STRICT HURDLE: Only take setups meeting confidence criteria
            for cand in candidates:
                if slots_available <= 0:
                    break
                if cand["score"] >= 51.5:  # High-conviction entry filter
                    req_cash = cand["qty"] * cand["entry"]
                    if capital >= req_cash:
                        capital -= req_cash
                        active_trades.append(cand)
                        slots_available -= 1

        # Portfolio valuation
        port_val = capital
        for tr in active_trades:
            if tr["symbol"] in processed_symbols and curr_date in processed_symbols[tr["symbol"]].index:
                port_val += tr["qty"] * float(processed_symbols[tr["symbol"]].loc[curr_date]["close"])
            else:
                port_val += tr["qty"] * tr["entry"]

        equity_curve.append({"date": curr_date, "equity": port_val})

    df_eq = pd.DataFrame(equity_curve)
    df_cls = pd.DataFrame(closed_trades)

    if df_cls.empty:
        return {"error": "No setups matched institutional scoring."}

    total_return = round(((df_eq["equity"].iloc[-1] - initial_capital) / initial_capital) * 100.0, 2)
    wins = len(df_cls[df_cls["pnl_rupees"] > 0])
    losses = len(df_cls[df_cls["pnl_rupees"] <= 0])
    win_rate = round((wins / len(df_cls)) * 100.0, 1)

    df_eq["ret"] = df_eq["equity"].pct_change().fillna(0)
    sharpe = 0.0
    if df_eq["ret"].std() > 0:
        sharpe = round(float((df_eq["ret"].mean() / df_eq["ret"].std()) * np.sqrt(252)), 2)

    return {
        "total_return_pct": total_return,
        "win_rate_pct": win_rate,
        "sharpe_ratio": sharpe,
        "total_trades": len(df_cls),
        "wins": wins,
        "losses": losses,
        "closed_trades": df_cls[[
            "symbol", "entry_date", "exit_date", "entry", "exit_price",
            "pnl_rupees", "pnl_pct", "status"
        ]]
    }