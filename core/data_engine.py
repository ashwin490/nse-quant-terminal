import duckdb
import pandas as pd
import yfinance as yf
from datetime import datetime

DB_PATH = "market_data.duckdb"

NIFTY_BASKET = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS", "AXISBANK.NS",
    "TATAMOTORS.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS", "BAJFINANCE.NS"
]

def init_database():
    """Initializes DuckDB tables for historical prices and predictions."""
    con = duckdb.connect(DB_PATH)
    
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_candles (
            symbol VARCHAR,
            date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (symbol, date)
        )
    """)
    
    con.execute("""
        CREATE TABLE IF NOT EXISTS prediction_audit (
            id BIGINT PRIMARY KEY,
            prediction_date DATE,
            symbol VARCHAR,
            entry_price DOUBLE,
            target_price DOUBLE,
            stop_loss DOUBLE,
            confidence_score DOUBLE,
            actual_outcome VARCHAR DEFAULT 'PENDING',
            pnl_percent DOUBLE DEFAULT 0.0
        )
    """)
    
    con.execute("CREATE SEQUENCE IF NOT EXISTS seq_pred_id START 1")
    con.close()
    print("✓ DuckDB tables ready.")

def sync_symbol_history(symbol: str, lookback_days: int = 365):
    """Downloads daily candle history for a single symbol and stores it in DuckDB."""
    con = duckdb.connect(DB_PATH)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=f"{lookback_days}d", interval="1d")
    
    if df.empty:
        con.close()
        return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.reset_index()
    df["date"] = pd.to_datetime(df["Date"]).dt.date
    df["symbol"] = symbol.replace(".NS", "")
    df["open"] = df["Open"].astype(float)
    df["high"] = df["High"].astype(float)
    df["low"] = df["Low"].astype(float)
    df["close"] = df["Close"].astype(float)
    df["volume"] = df["Volume"].astype(int)

    clean_df = df[["symbol", "date", "open", "high", "low", "close", "volume"]]

    con.register("staged_candles", clean_df)
    con.execute("""
        INSERT OR REPLACE INTO daily_candles 
        SELECT * FROM staged_candles
    """)
    con.close()

def sync_all_symbols(lookback_days: int = 365):
    """Syncs the entire basket into DuckDB."""
    init_database()
    print(f"Syncing {len(NIFTY_BASKET)} symbols ({lookback_days} days history)...")
    for sym in NIFTY_BASKET:
        print(f"  → Ingesting {sym}...")
        sync_symbol_history(sym, lookback_days=lookback_days)
    
    con = duckdb.connect(DB_PATH)
    total = con.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0]
    con.close()
    print(f"✓ All symbols synced. Total candles in DuckDB: {total}")

if __name__ == "__main__":
    sync_all_symbols(lookback_days=365)