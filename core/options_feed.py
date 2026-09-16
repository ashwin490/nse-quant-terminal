"""
NSE Options Chain Derivatives Analyzer.
Tracks Put-Call Ratio (PCR) to gauge institutional market sentiment.
"""

import requests

def get_options_pcr(symbol: str) -> float:
    """
    Fetches option chain Open Interest (OI) to calculate Put-Call Ratio (PCR).
    PCR > 1.3 indicates bullish accumulation; PCR < 0.7 indicates oversold/bearish.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
    
    try:
        session = requests.Session()
        # Initial request to hit NSE cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=5)
        response = session.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            # Extract totals from records
            filtered = data.get("filtered", {})
            ce_total_oi = filtered.get("CE", {}).get("totOI", 1)
            pe_total_oi = filtered.get("PE", {}).get("totOI", 1)
            
            pcr = round(pe_total_oi / ce_total_oi, 2)
            return pcr
    except Exception:
        pass
    
    return 1.0  # Default neutral PCR if API rate-limited