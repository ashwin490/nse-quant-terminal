import pandas as pd
import numpy as np
from datetime import date, timedelta
from jugaad_data.nse import stock_df

def fetch_delivery_metrics(symbol: str) -> dict:
    """
    Pulls historical Bhavcopy delivery data from the NSE.
    Returns the latest delivery % and a 20-day delivery volume shock multiplier.
    """
    clean_sym = symbol.replace(".NS", "")
    today = date.today()
    from_date = today - timedelta(days=45)

    try:
        # Fetch official security-wise delivery data from NSE archives
        df = stock_df(symbol=clean_sym, from_date=from_date, to_date=today, series="EQ")
        if df.empty or len(df) < 15:
            return {"deliv_pct": 35.0, "deliv_shock": 1.0}

        df = df.sort_values("DATE").reset_index(drop=True)
        
        # 'DELIV_QTY' and 'DELIV_PER' are standard columns in NSE Bhavcopy
        if "DELIV_QTY" in df.columns and "DELIV_PER" in df.columns:
            df["DELIV_QTY"] = pd.to_numeric(df["DELIV_QTY"], errors="coerce").fillna(0)
            df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce").fillna(0)
            
            latest_deliv_pct = float(df["DELIV_PER"].iloc[-1])
            avg_20_deliv_qty = float(df["DELIV_QTY"].iloc[-21:-1].mean())
            latest_deliv_qty = float(df["DELIV_QTY"].iloc[-1])
            
            deliv_shock = round(latest_deliv_qty / (avg_20_deliv_qty + 1e-9), 2)
            
            return {
                "deliv_pct": round(latest_deliv_pct, 1),
                "deliv_shock": deliv_shock
            }
    except Exception:
        # Graceful fallback if NSE rate-limits or market is on holiday
        pass

    return {"deliv_pct": 35.0, "deliv_shock": 1.0}

if __name__ == "__main__":
    print("Testing NSE Delivery Fetcher on RELIANCE...")
    res = fetch_delivery_metrics("RELIANCE")
    print(f"Delivery %: {res['deliv_pct']}% | Delivery Shock: {res['deliv_shock']}x")