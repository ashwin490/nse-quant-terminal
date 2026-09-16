import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import duckdb
import pandas as pd
import yfinance as yf
from core.data_engine import NIFTY_BASKET
from core.universe_sync import NIFTY_200_UNIVERSE

DB_PATH = "market_data.duckdb"

def sync_eod_market_data():
    print("==================================================")
    print(f"📦 Starting EOD Bhavcopy & Candle Ingestion: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")

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

    # Combine all symbols and clean syntax
    combined_universe = list(set([s.replace("$", "").replace(".NS", "") for s in (NIFTY_BASKET + NIFTY_200_UNIVERSE)]))
    combined_universe.append("^NSEI")  # Include benchmark

    yf_symbols = [f"{s}.NS" if s != "^NSEI" else s for s in combined_universe]

    print(f"📥 Batch downloading 5-day closing window for {len(yf_symbols)} tickers...")
    data = yf.download(yf_symbols, period="5d", interval="1d", group_by="ticker", progress=False)

    total_records = 0
    for sym_yf in yf_symbols:
        clean_sym = sym_yf.replace(".NS", "")
        try:
            if sym_yf in data and not data[sym_yf].empty:
                df_sym = data[sym_yf].dropna(subset=["Close"]).copy()
            else:
                continue

            for idx, row in df_sym.iterrows():
                trade_date = pd.to_datetime(idx).date()
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
                v = int(row["Volume"])

                con.execute("""
                    INSERT OR REPLACE INTO daily_candles (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [clean_sym, trade_date, o, h, l, c, v])
                total_records += 1
        except Exception:
            continue

    con.close()
    print(f"✅ Ingestion Complete! Synchronized {total_records} candle rows into DuckDB.")

if __name__ == "__main__":
    sync_eod_market_data()