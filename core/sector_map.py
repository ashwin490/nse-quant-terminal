"""
NSE Sector Mapping and Concentration Risk Engine.
Prevents portfolio over-concentration while ensuring unmapped stocks
are placed into independent industry groupings rather than a single bucket.
"""

NSE_SECTOR_MAP = {
    # IT / Tech
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "TECHM": "IT", "WIPRO": "IT", "LTIM": "IT",
    # Banking
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking", "AXISBANK": "Banking", "KOTAKBANK": "Banking", "INDUSINDBK": "Banking",
    # Energy / Oil & Gas
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy", "IOC": "Energy", "COALINDIA": "Energy", "GAIL": "Energy", "NTPC": "Power", "POWERGRID": "Power",
    # Auto
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto", "HEROMOTOCO": "Auto", "TVSMOTOR": "Auto",
    # FMCG / Consumer
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG", "TATACONSUM": "FMCG", "VBL": "FMCG",
    # Metals / Commodities
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals", "VEDL": "Metals",
    # Pharma / Healthcare
    "SUNPHARMA": "Pharma", "CIPLA": "Pharma", "DRREDDY": "Pharma", "DIVISLAB": "Pharma", "APOLLOHOSP": "Healthcare",
    # Financial Services / Insurance / NBFC
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials", "CHOLAFIN": "Financials", "SHRIRAMFIN": "Financials", "HDFCLIFE": "Insurance", "SBILIFE": "Insurance",
    # Infra / Industrials / Cement
    "LT": "Capital Goods", "ULTRACEMCO": "Cement", "GRASIM": "Cement", "AMBUJACEM": "Cement", "ABB": "Capital Goods", "SIEMENS": "Capital Goods", "BEL": "Defense", "HAL": "Defense",
    # Consumer Discretionary / Retail / Telecom
    "TITAN": "Consumer", "BHARTIARTL": "Telecom", "TRENT": "Retail", "DMART": "Retail", "NAUKRI": "Internet", "INDIGO": "Aviation", "HAVELLS": "Consumer Electricals", "PIDILITIND": "Chemicals", "ADANIENT": "Diversified", "ADANIPORTS": "Logistics", "DLF": "Real Estate"
}

def get_symbol_sector(raw_sym: str) -> str:
    clean = raw_sym.replace("$", "").replace(".NS", "").strip().upper()
    if clean in NSE_SECTOR_MAP:
        return NSE_SECTOR_MAP[clean]
    # Fallback to an isolated symbol category instead of a shared 'Diversified' bottleneck
    return f"Other_{clean}"

def apply_sector_concentration_cap(records: list, max_per_sector: int = 2) -> list:
    """
    Ensures no individual sector occupies more than max_per_sector allocations,
    while preserving the full candidate pool across different industries.
    """
    sector_tally = {}
    selected = []

    for r in records:
        sym = r.get("Ticker") or r.get("symbol") or ""
        sector = get_symbol_sector(sym)
        r["Sector"] = sector

        count = sector_tally.get(sector, 0)
        if count < max_per_sector:
            sector_tally[sector] = count + 1
            selected.append(r)

    return selected