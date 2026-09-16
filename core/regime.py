"""
Market Regime Analyzer with Error Guardrails.
"""

import yfinance as yf
import pandas as pd

def get_market_regime():
    """
    Fetches Nifty 50 data to determine market regime and applies error handling for empty data.
    """
    try:
        nifty = yf.download("^NSEI", period="30d", interval="1d", progress=False)
        
        # Handle multi-index columns if returned by yfinance
        if isinstance(nifty.columns, pd.MultiIndex):
            nifty.columns = [c[0] for c in nifty.columns]
            
        if nifty.empty or len(nifty) == 0:
            raise ValueError("Empty data returned")
            
        current_nifty = float(nifty["Close"].iloc[-1])
        ma20 = float(nifty["Close"].rolling(20).mean().iloc[-1])
        
        regime = "Bullish" if current_nifty >= ma20 else "Bearish"
        bias_multiplier = 1.05 if regime == "Bullish" else 0.85
        
        return {
            "regime": regime,
            "bias_multiplier": bias_multiplier,
            "current_nifty": current_nifty
        }
    except Exception:
        # Fallback default if API or rate limit fails
        return {
            "regime": "Neutral",
            "bias_multiplier": 1.0,
            "current_nifty": 0.0
        }