"""
Intraday VWAP & Momentum Confirmation Engine.
Fetches intraday bars (e.g., 15-minute intervals) to verify institutional accumulation.
"""

import yfinance as yf
import pandas as pd
import numpy as np

def check_vwap_momentum(symbol: str) -> dict:
    """
    Calculates intraday VWAP and checks if the latest price is holding above institutional VWAP.
    Returns a multiplier and confirmation status.
    """
    try:
        yf_sym = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
        # Fetch recent intraday data (e.g., 5-day history with 15-minute intervals)
        df = yf.download(yf_sym, period="5d", interval="15m", progress=False)
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
            
        if df.empty or len(df) < 10:
            return {"vwap_confirmed": True, "vwap_multiplier": 1.0}
            
        # Calculate VWAP: Cumulative(Typical Price * Volume) / Cumulative(Volume)
        df["Typical_Price"] = (df["High"] + df["Low"] + df["Close"]) / 3.0
        df["TP_Vol"] = df["Typical_Price"] * df["Volume"]
        
        # Reset daily accumulation or compute rolling window VWAP
        df["Cum_TP_Vol"] = df["TP_Vol"].rolling(26, min_periods=1).sum()
        df["Cum_Vol"] = df["Volume"].rolling(26, min_periods=1).sum()
        df["VWAP"] = df["Cum_TP_Vol"] / df["Cum_Vol"]
        
        latest_close = float(df["Close"].iloc[-1])
        latest_vwap = float(df["VWAP"].iloc[-1])
        
        # If price is above VWAP, institutions are net accumulators on intraday timeframe
        if latest_close >= latest_vwap:
            return {"vwap_confirmed": True, "vwap_multiplier": 1.05} # 5% confidence boost
        else:
            return {"vwap_confirmed": False, "vwap_multiplier": 0.90} # Penalty if trading below VWAP
            
    except Exception:
        return {"vwap_confirmed": True, "vwap_multiplier": 1.0}