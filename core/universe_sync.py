"""
Universe Synchronization & Broad Market Scanning Engine.
Fetches active NSE stock universes and filters equities based on price criteria.
"""

import duckdb

NIFTY_200_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "SBIN.NS",
    "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS", "AXISBANK.NS", "HINDUNILVR.NS",
    "ASIANPAINT.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS", "BAJFINANCE.NS", "NESTLEIND.NS",
    "ULTRACEMCO.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", "ONGC.NS", "NTPC.NS",
    "POWERGRID.NS", "TITAN.NS", "GRASIM.NS", "ADANIENT.NS", "ADANIPORTS.NS", "COALINDIA.NS",
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "DIVISLAB.NS", "DRREDDY.NS", "CIPLA.NS",
    "BPCL.NS", "IOC.NS", "SBILIFE.NS", "HDFCLIFE.NS", "BAJAJFINSV.NS", "TATAMOTORS.NS"
]

def get_sub_1000_universe() -> list:
    """
    Queries DuckDB for all available stocks whose latest close price is < ₹1,000.
    Allows the scanner to evaluate every qualifying share in the database without limits.
    """
    con = duckdb.connect("market_data.duckdb")
    
    query = """
        WITH latest_prices AS (
            SELECT symbol, close, 
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
            FROM daily_candles
            WHERE symbol NOT LIKE '^%'
        )
        SELECT symbol 
        FROM latest_prices 
        WHERE rn = 1 AND close < 1000.0
        ORDER BY close ASC
    """
    try:
        df_symbols = con.execute(query.strip()).fetchdf()
        symbols = df_symbols["symbol"].tolist()
    except Exception as e:
        print(f"Error fetching sub-1000 universe: {e}")
        symbols = []
    finally:
        con.close()
        
    return symbols