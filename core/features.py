import os
import duckdb
import pandas as pd
import numpy as np

DB_PATH = "market_data.duckdb"

FEATURE_COLS_V2 = [
    "dist_ema20_pct",
    "trend_spread_pct",
    "atr_pct",
    "rvol",
    "rsi_14",
    "deliv_shock",
    "rel_strength_nifty",
    "deliv_trend_ratio",
    "breakout_clearance"
]

def extract_features(symbol: str, deliv_shock: float = 1.0, deliv_pct: float = 40.0) -> pd.DataFrame:
    con = duckdb.connect(DB_PATH)
    clean_sym = symbol.replace("$", "").replace(".NS", "")
    
    # Load target symbol and Nifty benchmark candles
    df = con.execute(f"""
        SELECT date, open, high, low, close, volume 
        FROM daily_candles 
        WHERE symbol = '{clean_sym}' 
        ORDER BY date ASC
    """).fetchdf()

    df_nifty = con.execute("""
        SELECT date, close as nifty_close 
        FROM daily_candles 
        WHERE symbol IN ('^NSEI', 'NIFTY', 'NIFTY50')
        ORDER BY date ASC
    """).fetchdf()
    con.close()

    if df.empty or len(df) < 50:
        return pd.DataFrame()

    df = df.sort_values("date").reset_index(drop=True)

    # Core Trend Metrics
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["dist_ema20_pct"] = ((df["close"] - df["ema20"]) / df["ema20"]) * 100.0
    df["trend_spread_pct"] = ((df["ema20"] - df["ema50"]) / df["ema50"]) * 100.0

    # Volatility (ATR 14)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean().bfill()
    df["atr_pct"] = (df["atr_14"] / df["close"]) * 100.0

    # Relative Volume
    vol_sma20 = df["volume"].rolling(20).mean().replace(0, np.nan)
    df["rvol"] = (df["volume"] / vol_sma20).fillna(1.0)

    # Momentum (RSI 14)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # Factor 6: Delivery Shock Proxy
    df["deliv_shock"] = float(deliv_shock)

    # Factor 7: Relative Strength vs NIFTY (20-Day Ratio Return)
    if not df_nifty.empty:
        merged = pd.merge(df, df_nifty, on="date", how="left").ffill()
        stock_ret_20 = (merged["close"] / merged["close"].shift(20)) - 1.0
        nifty_ret_20 = (merged["nifty_close"] / merged["nifty_close"].shift(20)) - 1.0
        df["rel_strength_nifty"] = ((stock_ret_20 - nifty_ret_20) * 100.0).fillna(0.0)
    else:
        df["rel_strength_nifty"] = 0.0

    # Factor 8: Institutional Delivery Trend Ratio (5-day vs 20-day volume acceleration)
    vol_sma5 = df["volume"].rolling(5).mean().replace(0, np.nan)
    df["deliv_trend_ratio"] = (vol_sma5 / vol_sma20).fillna(1.0).clip(0.5, 3.0)

    # Factor 9: Prior-Day Supply Clearance (Close above previous Day High)
    prev_high = df["high"].shift(1)
    df["breakout_clearance"] = (((df["close"] - prev_high) / df["close"]) * 100.0).fillna(0.0)

    return df