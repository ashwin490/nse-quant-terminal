"""
Macroeconomic & Alternative Data Feed.
Pulls live market fear indices (India VIX) and macro indicators to adjust risk scoring.
"""

import yfinance as yf
import pandas as pd

def get_macro_risk_adjuster() -> float:
    """
    Fetches India VIX (^INDIAVIX) or market volatility indicators 
    to return a macro risk multiplier. 
    If volatility spikes (fear is high), multiplier scales down exposure.
    """
    try:
        vix_data = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        if isinstance(vix_data.columns, pd.MultiIndex):
            vix_data.columns = [c[0] for c in vix_data.columns]
            
        if not vix_data.empty:
            current_vix = float(vix_data["Close"].iloc[-1])
            # If VIX is above 20 (high fear/volatility), apply a defensive penalty multiplier
            if current_vix > 22.0:
                return 0.85 # Reduce conviction scoring in high-fear regimes
            elif current_vix < 13.0:
                return 1.10 # Boost confidence in low-volatility bull runs
    except Exception:
        pass
        
    return 1.0