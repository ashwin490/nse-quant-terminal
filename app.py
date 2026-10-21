import os
import re
import sys
import math
import json
import time
import hmac
import html
import hashlib
import calendar
import threading
import warnings
import io
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, time as dtime, timedelta
import random
import requests

import streamlit as st
import pandas as pd
import numpy as np
import joblib
import pytz
import duckdb

try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except ImportError:
    go = None
    PLOTLY_OK = False

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

warnings.filterwarnings("ignore")

# set_page_config must be the first Streamlit command that renders anything
st.set_page_config(
    page_title="Autonomous AI Quant Terminal (NSE)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ==============================================================================
# 0. SETTINGS — everything you might ever want to change lives in this block
# ==============================================================================
DB_PATH = os.path.join(ROOT_DIR, "market_data.duckdb")
MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker.pkl")          # your original model: NEVER overwritten
LIVE_MODEL_PATH = os.path.join(ROOT_DIR, "models", "lgbm_stock_ranker_live.pkl")  # upgraded model (only if it tests better)

# ---- Portfolio & risk rules ----
POSITION_SIZE_INR = 25000.0
MAX_DAILY_EQUITY_TRADES = 5
MAX_HOLD_CALENDAR_DAYS = 7
BREAKEVEN_TRIGGER_RATIO = 0.65
MAX_ACTIVE_PER_SECTOR = 2
MAX_ACTIVE_OTHER_SECTOR = 4            # shares we couldn't classify share one bigger bucket
MAX_OPEN_POSITIONS = 20
MAX_OPEN_RISK_INR = 7000.0
COOLDOWN_CALENDAR_DAYS = 3
HALF_LIFE_DAYS = 30.0
# ---- Whole-market scan (every NSE share in the EQ series) ----
SCAN_WORKERS = 12                      # parallel price downloads
SCAN_TIME_BUDGET_SEC = 240             # stop downloading after this long; the rest is checked next time
SWEEP_TTL_SEC = 900                    # re-download a tradable share's prices at most every 15 min
QUIET_TTL_SEC = 21600                  # thinly traded shares are re-checked every 6 hours
FRESH_PRICE_TTL_SEC = 60               # finalists get a fresh price (≤1 min old) before any buy
FRESH_PRICE_MAX_AGE_SEC = 180          # ...and are skipped if that fresh download failed (never buy on an old price)
MAX_DEEP_CHECKS = 40                   # finalists that get the full news / memory / market check
DEEP_CHECK_MARGIN = 12.0               # finalists must be within this many points of the confidence bar
MAX_ATR_PCT = 7.0                      # skip shares that swing more than this % on a normal day
MIN_LISTED_FOR_VALID_LIST = 500        # a downloaded share list smaller than this is treated as broken
PULSE_REFRESH_SEC = 1800               # after today's picks are used up, still refresh the market pulse every 30 min
STRESS_NIFTY_5D = -3.0                 # pause buying if Nifty falls this much in 5 days ...
STRESS_VIX = 25.0                      # ... or India VIX is at/above this

# ---- Entry realism & quality filters ----
ENTRY_WINDOW_START = dtime(9, 30)
ENTRY_WINDOW_END = dtime(15, 0)
MIN_CALIBRATED_CONFIDENCE = 60.0
MIN_RVOL_THRESHOLD = 0.45
MIN_NET_RETURN_PCT = 1.10
MIN_PRICE_INR = 10.0
MIN_AVG_TURNOVER_INR = 20_000_000      # ₹2 crore traded per day
BE_TOLERANCE_PCT = 0.5

# ---- Costs (approximate NSE delivery-trade charges; edit if rates change) ----
STT_PCT_PER_SIDE = 0.100
STAMP_DUTY_PCT_BUY = 0.015
EXCHANGE_TXN_PCT_PER_SIDE = 0.00297
SEBI_PCT_PER_SIDE = 0.0001
GST_PCT = 18.0
DEFAULT_BROKERAGE_PER_ORDER_INR = 0.0
DEFAULT_DP_CHARGE_INR = 15.9           # depository charge per sale (varies by broker)
DEFAULT_FRICTION_PCT = 0.28            # used for old trades that didn't store their own

# ---- Options (practice) ----
OPTION_BUDGET_INR = 30000.0
RISK_FREE_RATE = 0.06
OPTION_MODEL_HALF_SPREAD = 0.03
OPTION_MAX_SPREAD_PCT = 15.0
OPTION_MIN_OPEN_INTEREST = 50
OPTION_TARGET_MULT = 1.65
OPTION_STOP_MULT = 0.50
# NSE revises lot sizes now and then — VERIFY these against NSE's current contract list.
FNO_STOCKS = ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS"]
LOT_SIZES = {"RELIANCE": 250, "HDFCBANK": 400, "ICICIBANK": 700, "INFY": 400, "TCS": 175, "SBIN": 750, "BHARTIARTL": 950, "ITC": 1600, "LT": 300}
# ---- Options learner (separate from the share AI; it only ever changes the options idea) ----
OPT_LEARN_MIN_TRADES = 10              # finished option ideas (in the window below) before it becomes pickier
OPT_LEARN_WINDOW_DAYS = 60             # only the last 60 days count, so a strict rule lifts by itself and gets re-tested
OPT_REST_THRESHOLD = 0.75              # a share 'rests' from options after a recent loss (one loss ≈ 12 days)
OPT_LIVE_ONLY_MIN_MODEL = 8            # estimated-price ideas needed before it may decide to use real NSE quotes only
OPT_MAX_TREND_BAR = 82.0               # the pickiest it can get (trend strength needed)
STRIKE_STEPS = {"RELIANCE": 20, "HDFCBANK": 10, "ICICIBANK": 10, "INFY": 20, "TCS": 50, "SBIN": 10, "BHARTIARTL": 20, "ITC": 10, "LT": 50}

# ---- AI governance ----
MIN_SETTLED_TO_RETRAIN = 150
RETRAIN_STEP_INTERVAL = 50
MIN_CALIBRATION_SAMPLES = 80
CONF_SHRINK = 0.5

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

yf_session = requests.Session()
yf_session.headers.update({
    "User-Agent": USER_AGENTS[0],
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
    "Connection": "keep-alive"
})
if hasattr(yf_session, "mount"):
    try:
        from requests.adapters import HTTPAdapter
        yf_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=SCAN_WORKERS + 8, max_retries=0))
    except Exception:
        pass

@st.cache_resource
def get_db_lock():
    return threading.Lock()

DB_LOCK = get_db_lock()

@st.cache_resource
def get_hist_store():
    return {"lock": threading.Lock(), "data": {}}

@st.cache_resource
def get_nse_http_box():
    return {"session": None, "ts": 0.0, "lock": threading.Lock()}

# ---- Emergency backup list: used ONLY if NSE's full share list can't be downloaded and no saved copy exists ----
FALLBACK_SHARES = [
    "SBIN.NS", "ITC.NS", "TATAPOWER.NS", "BEL.NS", "NTPC.NS",
    "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS", "PFC.NS", "RECLTD.NS",
    "BHEL.NS", "TATASTEEL.NS", "VEDL.NS", "HINDALCO.NS", "WIPRO.NS",
    "TATAMOTORS.NS", "BANKBARODA.NS", "PNB.NS", "CANBK.NS", "UNIONBANK.NS",
    "IDFCFIRSTB.NS", "FEDERALBNK.NS", "ASHOKLEY.NS", "MOTHERSON.NS", "EXIDEIND.NS",
    "GAIL.NS", "IOC.NS", "BPCL.NS", "PETRONET.NS", "IGL.NS",
    "BIOCON.NS", "LAURUSLABS.NS", "GRANULES.NS", "NATIONALUM.NS", "NMDC.NS",
    "SAIL.NS", "JINDALSTEL.NS", "IRCTC.NS", "CONCOR.NS", "IRFC.NS",
    "RVNL.NS", "NHPC.NS", "SJVN.NS", "CESC.NS", "DABUR.NS",
    "MARICO.NS", "GODREJCP.NS", "BERGEPAINT.NS", "ZEEL.NS", "MANAPPURAM.NS"
]

NSE_SECTOR_MAP = {
    "RELIANCE": "Energy & Conglomerate", "ONGC": "Energy & Conglomerate", "GAIL": "Energy & Conglomerate",
    "IOC": "Energy & Conglomerate", "BPCL": "Energy & Conglomerate", "PETRONET": "Energy & Conglomerate",
    "IGL": "Energy & Conglomerate", "NTPC": "Utilities & Power", "POWERGRID": "Utilities & Power",
    "TATAPOWER": "Utilities & Power", "NHPC": "Utilities & Power", "SJVN": "Utilities & Power",
    "CESC": "Utilities & Power", "COALINDIA": "Basic Materials", "HDFCBANK": "Financials",
    "ICICIBANK": "Financials", "SBIN": "Financials", "KOTAKBANK": "Financials", "AXISBANK": "Financials",
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials", "PFC": "Financials", "RECLTD": "Financials",
    "BANKBARODA": "Financials", "PNB": "Financials", "CANBK": "Financials", "UNIONBANK": "Financials",
    "IDFCFIRSTB": "Financials", "FEDERALBNK": "Financials", "IRFC": "Financials", "MANAPPURAM": "Financials",
    "INFY": "IT & Technology", "TCS": "IT & Technology", "HCLTECH": "IT & Technology", "WIPRO": "IT & Technology",
    "TECHM": "IT & Technology", "BHARTIARTL": "Telecom", "ZEEL": "Telecom", "ITC": "Consumer & FMCG",
    "HINDUNILVR": "Consumer & FMCG", "TATACONSUM": "Consumer & FMCG", "NESTLEIND": "Consumer & FMCG",
    "DABUR": "Consumer & FMCG", "MARICO": "Consumer & FMCG", "GODREJCP": "Consumer & FMCG",
    "BERGEPAINT": "Consumer & FMCG", "IRCTC": "Consumer & FMCG", "LT": "Industrials & Infra",
    "BEL": "Defense & Aero", "HAL": "Defense & Aero", "BHEL": "Industrials & Infra",
    "CONCOR": "Industrials & Infra", "RVNL": "Industrials & Infra", "TATAMOTORS": "Auto",
    "M&M": "Auto", "MARUTI": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto", "ASHOKLEY": "Auto",
    "MOTHERSON": "Auto", "EXIDEIND": "Auto", "SUNPHARMA": "Healthcare", "DRREDDY": "Healthcare",
    "CIPLA": "Healthcare", "BIOCON": "Healthcare", "LAURUSLABS": "Healthcare", "GRANULES": "Healthcare",
    "TATASTEEL": "Metals & Mining", "JSWSTEEL": "Metals & Mining", "HINDALCO": "Metals & Mining",
    "VEDL": "Metals & Mining", "NATIONALUM": "Metals & Mining", "NMDC": "Metals & Mining",
    "SAIL": "Metals & Mining", "JINDALSTEL": "Metals & Mining"
}

INR_IT_PHARMA_EXPORTERS = {"INFY", "TCS", "HCLTECH", "WIPRO", "TECHM", "SUNPHARMA", "DRREDDY", "CIPLA", "BIOCON", "LAURUSLABS", "GRANULES"}
CRUDE_SENSITIVE_USERS = {"ASIANPAINT", "BERGEPAINT", "INDIGO", "BPCL", "HPCL", "IOC"}

NSE_BULLISH_LEXICON = {
    "order win": 6.0, "letter of award": 7.0, "loa": 5.0, "l1 bidder": 7.0,
    "new contract": 5.0, "share buyback": 6.0, "bonus issue": 5.0, "stock split": 4.0,
    "usfda approval": 8.0, "tentative approval": 5.0, "eir": 6.0, "pli scheme": 5.0,
    "promoter buying": 6.0, "rating upgrade": 5.0, "record revenue": 6.0,
    "joint venture": 4.0, "capacity expansion": 4.0, "debt reduction": 5.0
}

NSE_BEARISH_LEXICON = {
    "sebi notice": -25.0, "show cause": -25.0, "sebi order": -25.0,
    "ed raid": -30.0, "enforcement directorate": -30.0, "income tax search": -25.0,
    "form 483": -22.0, "import alert": -28.0, "oai": -25.0,
    "promoter pledge": -18.0, "invocation of pledge": -28.0,
    "qip floor": -15.0, "offer for sale": -15.0, "ofs": -15.0, "dilution": -20.0,
    "auditor resignation": -30.0, "default": -30.0, "nclt": -30.0, "insolvency": -35.0,
    "rating downgrade": -20.0, "fraud": -35.0, "penalty": -15.0, "plant shutdown": -20.0
}
# Whole-word matching (so "loa" no longer fires inside "loan", or "eir" inside "their")
_BULL_PATTERNS = [(re.compile(r"\b" + re.escape(p) + r"\b"), p, w) for p, w in NSE_BULLISH_LEXICON.items()]
_BEAR_PATTERNS = [(re.compile(r"\b" + re.escape(p) + r"\b"), p, w) for p, w in NSE_BEARISH_LEXICON.items()]

# ==============================================================================
# 0b. SMALL HELPERS
# ==============================================================================
def ist_now() -> datetime:
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def esc(x) -> str:
    return html.escape(str(x))

def _collapse(s: str) -> str:
    return re.sub(r"\s*\n\s*", "", s.strip())

def _parse_features(f) -> dict:
    if isinstance(f, dict):
        return f
    try:
        obj = json.loads(f) if f else {}
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def _to_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def _first_valid(*vals):
    for v in vals:
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        sv = str(v).strip()
        if sv and sv.lower() not in ("nan", "none", "nat"):
            return sv
    return ""

def clean_sym_name(sym: str) -> str:
    raw = str(sym).strip().lstrip("$")
    base_token = raw.split()[0] if raw else ""
    return base_token.replace(".NS", "").replace(".BO", "")

def normalize_ts_str(val, fallback_str: str) -> str:
    if val is None or pd.isna(val):
        return fallback_str
    s = str(val).strip().replace("T", " ").replace(" IST", "")
    if "+" in s:
        s = s.split("+")[0].strip()
    if "." in s:
        s = s.split(".")[0].strip()
    return s[:19] if len(s) >= 10 else fallback_str

def get_ticker_sector(ticker: str) -> str:
    t = clean_sym_name(ticker).upper()
    if t in NSE_SECTOR_MAP:
        return NSE_SECTOR_MAP[t]
    return get_nse_industry_map().get(t, "Other")

def sector_cap(sector: str) -> int:
    return MAX_ACTIVE_OTHER_SECTOR if sector == "Other" else MAX_ACTIVE_PER_SECTOR

def get_trade_sector(ticker: str, f_json=None) -> str:
    t = clean_sym_name(ticker).upper()
    if t in NSE_SECTOR_MAP:
        return NSE_SECTOR_MAP[t]
    f = _parse_features(f_json)
    stored = str(f.get("sector") or "")
    if stored and stored != "Other":
        return stored
    return get_nse_industry_map().get(t, "Other")

def classify_outcome(status) -> str:
    s = str(status).upper()
    if "WIN" in s:
        return "WIN"
    if "BREAK-EVEN" in s:
        return "BE"
    if "LOSS" in s:
        return "LOSS"
    if "EXPIRED" in s:
        return "EXPIRED"
    if "ACTIVE" in s:
        return "ACTIVE"
    return "OTHER"

def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)

def is_nse_market_open() -> bool:
    now_ist = ist_now()
    return get_market_state()["trading_day"] and dtime(9, 15) <= now_ist.time() <= dtime(15, 30)

def is_entry_window_open() -> bool:
    now_ist = ist_now()
    return get_market_state()["trading_day"] and ENTRY_WINDOW_START <= now_ist.time() <= ENTRY_WINDOW_END

def normalize_ticker_for_yf(ticker_str: str) -> str:
    clean = str(ticker_str).split()[0].strip()
    if clean.endswith(".NS") or clean.endswith(".BO") or clean.startswith("^") or "=" in clean:
        return clean
    return f"{clean}.NS"

# ---------- costs ----------
def get_brokerage_inr() -> float:
    try:
        return float(st.session_state.get("brokerage_inr", DEFAULT_BROKERAGE_PER_ORDER_INR))
    except Exception:
        return DEFAULT_BROKERAGE_PER_ORDER_INR

def get_dp_charge_inr() -> float:
    try:
        return float(st.session_state.get("dp_charge_inr", DEFAULT_DP_CHARGE_INR))
    except Exception:
        return DEFAULT_DP_CHARGE_INR

def slippage_pct_per_side(avg_turnover_inr) -> float:
    try:
        v = float(avg_turnover_inr)
        if not math.isfinite(v):
            raise ValueError
        if v >= 500_000_000:
            return 0.03
        if v >= 100_000_000:
            return 0.05
        if v >= 20_000_000:
            return 0.10
        return 0.20
    except Exception:
        return 0.10

def compute_costs_pct(position_value: float, slip_per_side_pct: float, brokerage=None, dp=None) -> float:
    """Round-trip cost of a delivery trade (buy then sell) as a % of the position."""
    v = max(float(position_value), 1.0)
    b = get_brokerage_inr() if brokerage is None else float(brokerage)
    d = get_dp_charge_inr() if dp is None else float(dp)
    stt = STT_PCT_PER_SIDE / 100.0 * v * 2
    stamp = STAMP_DUTY_PCT_BUY / 100.0 * v
    exch = EXCHANGE_TXN_PCT_PER_SIDE / 100.0 * v * 2
    sebi = SEBI_PCT_PER_SIDE / 100.0 * v * 2
    gst = GST_PCT / 100.0 * (2 * b + exch + sebi)
    slip = slip_per_side_pct / 100.0 * v * 2
    total = stt + stamp + exch + sebi + gst + 2 * b + d + slip
    return round(total / v * 100.0, 3)

def get_trade_friction_pct(f_json) -> float:
    f = _parse_features(f_json)
    try:
        return float(f["friction_pct"]) if "friction_pct" in f else DEFAULT_FRICTION_PCT
    except Exception:
        return DEFAULT_FRICTION_PCT

def get_breakeven_exit_price(entry_price: float, friction_pct: float) -> float:
    return round(float(entry_price) * (1.0 + (float(friction_pct) / 100.0)), 2)

def calc_net_equity_pnl_pct(entry_price: float, current_price: float, friction_pct: float) -> float:
    if entry_price <= 0:
        return 0.0
    gross_pct = ((float(current_price) - float(entry_price)) / float(entry_price)) * 100.0
    return round(gross_pct - float(friction_pct), 2)

def is_stop_breakeven_protected(entry_price: float, stop_loss: float) -> bool:
    return float(stop_loss) >= float(entry_price) - 1e-4

def classify_stop_exit(entry: float, stop_at_hit: float, fill: float, friction_pct: float) -> str:
    net = calc_net_equity_pnl_pct(entry, fill, friction_pct)
    if stop_at_hit >= entry - 1e-4 and net >= -BE_TOLERANCE_PCT:
        return "🛡️ BREAK-EVEN (PROTECTED EXIT)"
    return "🛑 LOSS (STOPPED OUT)"

def evaluate_completed_bar(o, hi, lo, c, entry, target, stop, be_floor, be_trigger):
    """One COMPLETED daily bar: gap-aware, stop-first on ambiguity, break-even ratchet only affects the NEXT bar."""
    if o >= target:
        return "WIN", o, stop
    if o <= stop:
        return "STOP", o, stop
    if lo <= stop:
        return "STOP", stop, stop
    if hi >= target:
        return "WIN", target, stop
    new_stop = stop
    if target > entry and stop < be_floor and hi >= be_trigger and c > be_floor:
        new_stop = be_floor
    return None, c, new_stop

# ==============================================================================
# 0c. THE WHOLE NSE MARKET: SHARE LIST + INDUSTRY LABELS (downloaded, saved, with fallbacks)
# ==============================================================================
NSE_EQUITY_LIST_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]
NSE_INDUSTRY_LIST_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
UNIVERSE_SAVE_PATH = os.path.join(ROOT_DIR, "nse_all_shares.json")
INDUSTRY_SAVE_PATH = os.path.join(ROOT_DIR, "nse_industries.json")
_SYM_OK = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
# NSE industry names -> the app's sector names (anything unmatched keeps NSE's own name)
_INDUSTRY_TO_SECTOR = [
    ("financial", "Financials"), ("information technology", "IT & Technology"), ("oil gas", "Energy & Conglomerate"),
    ("fast moving consumer", "Consumer & FMCG"), ("automobile", "Auto"), ("capital goods", "Industrials & Infra"),
    ("construction materials", "Basic Materials"), ("construction", "Industrials & Infra"), ("healthcare", "Healthcare"),
    ("metals", "Metals & Mining"), ("power", "Utilities & Power"), ("utilities", "Utilities & Power"),
    ("telecom", "Telecom"), ("chemical", "Chemicals"), ("consumer durables", "Consumer Durables"),
    ("consumer services", "Consumer Services"), ("realty", "Realty"), ("media", "Media"), ("textile", "Textiles"),
    ("forest", "Basic Materials"), ("diversified", "Energy & Conglomerate"), ("services", "Services"),
]

def _industry_to_sector(industry: str) -> str:
    low = str(industry).strip().lower()
    if not low or low in ("nan", "none"):
        return "Other"
    for key, label in _INDUSTRY_TO_SECTOR:
        if key in low:
            return label
    return str(industry).strip().title()[:30]

def _http_text(url: str, timeout: int = 10) -> str:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENTS[0], "Accept": "text/csv,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": "https://www.nseindia.com/"})
    try:
        r = s.get(url, timeout=timeout)
        if r.status_code == 200:
            return str(getattr(r, "text", "") or "")
    except Exception:
        pass
    return ""

def _read_csv_text(text: str) -> pd.DataFrame:
    if not text or "," not in text:
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str)
    except Exception:
        return pd.DataFrame()
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df

def parse_equity_list(text: str) -> list:
    """NSE's EQUITY_L.csv -> sorted list of symbols in the normal 'EQ' series (excludes trade-to-trade/surveillance series)."""
    df = _read_csv_text(text)
    if df.empty or "SYMBOL" not in df.columns:
        return []
    if "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip().str.upper() == "EQ"]
    syms = {str(x).strip().upper() for x in df["SYMBOL"].tolist()}
    return sorted(x for x in syms if _SYM_OK.match(x))

def _save_json(path: str, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except Exception:
        pass

def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None

def get_all_nse_symbols() -> tuple:
    """(symbols, where_from). Today's NSE list if reachable; otherwise the last saved copy; otherwise a small backup list.
    Cached for 12 hours (2 hours if NSE didn't answer)."""
    box = get_nse_http_box()
    now = time.time()
    cached = box.get("universe")
    if cached and now - cached[0] < (43200 if cached[3] else 7200):
        return cached[1], cached[2]
    syms, source, fresh = [], "", False
    for url in NSE_EQUITY_LIST_URLS:
        syms = parse_equity_list(_http_text(url, timeout=8))
        if len(syms) >= MIN_LISTED_FOR_VALID_LIST:
            source, fresh = "NSE's full share list (today)", True
            _save_json(UNIVERSE_SAVE_PATH, {"saved": ist_now().strftime("%Y-%m-%d %H:%M"), "symbols": syms})
            break
        syms = []
    if not syms:
        saved = _load_json(UNIVERSE_SAVE_PATH)
        if isinstance(saved, dict) and isinstance(saved.get("symbols"), list) and len(saved["symbols"]) >= MIN_LISTED_FOR_VALID_LIST:
            syms = sorted({str(x).strip().upper() for x in saved["symbols"] if _SYM_OK.match(str(x).strip().upper())})
            source = f"saved NSE share list from {saved.get('saved', 'earlier')} (NSE not reachable right now)"
    if not syms:
        syms = sorted({clean_sym_name(x).upper() for x in (FALLBACK_SHARES + DEFAULT_NIFTY_BASKET)})
        source = "small backup list — NSE's full list couldn't be downloaded"
    box["universe"] = (now, syms, source, fresh)
    return syms, source

def get_nse_industry_map() -> dict:
    """{'SYMBOL': 'Sector'} from NSE's index constituent lists (covers the ~750 most traded shares). Saved to disk; cached."""
    box = get_nse_http_box()
    now = time.time()
    cached = box.get("industries")
    if cached and now - cached[0] < (43200 if cached[2] else 7200):
        return cached[1]
    box["industries"] = (now, (cached[1] if cached else {}), False)      # stops re-entry while downloading
    out = {}
    for url in NSE_INDUSTRY_LIST_URLS:
        df = _read_csv_text(_http_text(url, timeout=6))
        if not df.empty and "SYMBOL" in df.columns and "INDUSTRY" in df.columns:
            for sym, ind in zip(df["SYMBOL"].tolist(), df["INDUSTRY"].tolist()):
                k = str(sym).strip().upper()
                if _SYM_OK.match(k) and k not in out:
                    out[k] = _industry_to_sector(ind)
        if len(out) >= 700:
            break
    fresh = len(out) >= 300
    if fresh:
        _save_json(INDUSTRY_SAVE_PATH, out)
    else:
        saved = _load_json(INDUSTRY_SAVE_PATH)
        out = saved if isinstance(saved, dict) and saved else out
    box["industries"] = (now, out, fresh)
    return out

# ==============================================================================
# 1. DATABASE (THREAD-SAFE DUCKDB)
# ==============================================================================
def init_duckdb_storage():
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    trade_id VARCHAR PRIMARY KEY,
                    timestamp VARCHAR,
                    date_str VARCHAR,
                    ticker VARCHAR,
                    asset_type VARCHAR DEFAULT 'EQUITY',
                    entry_price DOUBLE,
                    target_price DOUBLE,
                    stop_loss DOUBLE,
                    shares INTEGER,
                    capital_allocated DOUBLE,
                    status VARCHAR DEFAULT 'ACTIVE',
                    latest_price DOUBLE,
                    pnl_pct DOUBLE DEFAULT 0.0,
                    exit_price DOUBLE DEFAULT 0.0,
                    exit_timestamp VARCHAR,
                    last_audited VARCHAR,
                    features_json VARCHAR DEFAULT '{}'
                )
            """)
            try:
                con.execute("ALTER TABLE trade_journal ADD COLUMN features_json VARCHAR DEFAULT '{}'")
            except Exception:
                pass
            con.execute("""
                CREATE TABLE IF NOT EXISTS daily_options_journal (
                    date_key VARCHAR PRIMARY KEY,
                    timestamp VARCHAR,
                    share_name VARCHAR,
                    option_contract VARCHAR,
                    strike_price DOUBLE,
                    expiry_days INTEGER,
                    underlying_spot DOUBLE,
                    lot_size INTEGER,
                    entry_premium DOUBLE,
                    current_option_price DOUBLE,
                    target_premium DOUBLE,
                    stop_loss_premium DOUBLE,
                    total_capital DOUBLE,
                    ai_confidence DOUBLE,
                    implied_vol DOUBLE,
                    status VARCHAR DEFAULT 'ACTIVE',
                    pnl_pct DOUBLE DEFAULT 0.0,
                    last_audited VARCHAR
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS system_metadata (
                    meta_key VARCHAR PRIMARY KEY,
                    meta_val VARCHAR
                )
            """)
            con.execute("DELETE FROM trade_journal WHERE ticker LIKE '%.L'")
        except Exception:
            pass
        for tbl in ("trade_journal", "daily_options_journal"):
            try:   # any "open" row spelled differently (emoji, lower-case, blank) is normalised to plain ACTIVE
                con.execute(f"UPDATE {tbl} SET status = 'ACTIVE' WHERE status IS NULL OR (UPPER(status) LIKE '%ACTIVE%' AND status != 'ACTIVE')")
            except Exception:
                pass
        con.close()

init_duckdb_storage()

def journal_counts() -> dict:
    """Plain counts straight from the journal (used for the health check)."""
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            df = con.execute("SELECT status FROM trade_journal WHERE ticker NOT LIKE '%.L'").df()
            n_open = int((df["status"].apply(classify_outcome) == "ACTIVE").sum()) if not df.empty else 0
            return {"total": len(df), "open": n_open, "closed": len(df) - n_open}
        except Exception:
            return {"total": 0, "open": 0, "closed": 0}
        finally:
            con.close()

def get_meta(key: str, default=None):
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            df = con.execute("SELECT meta_val FROM system_metadata WHERE meta_key = ?", [key]).df()
            return df["meta_val"].iloc[0] if not df.empty else default
        except Exception:
            return default
        finally:
            con.close()

def set_meta(key: str, val: str):
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            con.execute("INSERT OR REPLACE INTO system_metadata VALUES (?, ?)", [key, str(val)])
        except Exception:
            pass
        finally:
            con.close()

# ==============================================================================
# 2. SAFE IMPORTS OF YOUR OTHER MODULES (WITH FALLBACKS)
# ==============================================================================
DEFAULT_NIFTY_BASKET = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS", "AXISBANK.NS",
    "KOTAKBANK.NS", "BAJFINANCE.NS", "MARUTI.NS", "SUNPHARMA.NS", "NTPC.NS",
    "TATAMOTORS.NS", "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS", "BEL.NS",
    "TATAPOWER.NS", "PFC.NS", "RECLTD.NS", "BHEL.NS", "TATASTEEL.NS",
    "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "WIPRO.NS", "HCLTECH.NS"
]

ANNOUNCEMENTS_AVAILABLE = True
try:
    from core.announcements import check_corporate_announcements
except Exception:
    ANNOUNCEMENTS_AVAILABLE = False
    def check_corporate_announcements(sym: str):
        return {"risk_penalty": 1.0, "headline": "Standard NSE Exchange Flow"}

try:
    from supabase import create_client, Client
except ImportError:
    create_client, Client = None, None

FEATURE_COLS = [
    "dist_ema20_pct", "trend_spread_pct", "atr_pct", "rvol", "rsi_14", "deliv_shock"
]

# ==============================================================================
# 3. HARDENED ZERO-TRUST AUTHENTICATION  (kept exactly as you had it)
# ==============================================================================
def check_password() -> bool:
    if st.query_params.get("auth") == "QuantTerminal2026" or st.session_state.get("password_correct", False):
        st.session_state["password_correct"] = True
        return True

    now_ts = time.time()
    lockout_until = st.session_state.get("auth_lockout_until", 0.0)
    if now_ts < lockout_until:
        rem_min = int(math.ceil((lockout_until - now_ts) / 60.0))
        st.error(f"🔒 Terminal Locked due to repeated failed attempts. Try again in {rem_min} minute(s).")
        return False

    expected_user = st.secrets.get("AUTH_USER", "admin") if hasattr(st, "secrets") else "admin"
    default_hash = hashlib.sha256("QuantTerminal2026!".encode("utf-8")).hexdigest()
    expected_hash = st.secrets.get("AUTH_PASS_HASH", default_hash) if hasattr(st, "secrets") else default_hash

    st.subheader("🔐 Autonomous NSE Quant Terminal — Restricted Institutional Access")
    with st.form("nse_secure_login_form", clear_on_submit=True):
        user_in = st.text_input("Operator Username")
        pass_in = st.text_input("Cryptographic Passkey", type="password")
        submitted = st.form_submit_button("Authenticate Session", width="stretch", type="primary")

        if submitted:
            input_hash = hashlib.sha256(pass_in.encode("utf-8")).hexdigest()
            user_ok = hmac.compare_digest(user_in.strip(), str(expected_user).strip())
            pass_ok = hmac.compare_digest(input_hash, str(expected_hash).strip().lower())

            if user_ok and pass_ok:
                st.session_state["password_correct"] = True
                st.session_state["failed_auth_attempts"] = 0
                st.query_params["auth"] = "QuantTerminal2026"
                st.rerun()
            else:
                fails = st.session_state.get("failed_auth_attempts", 0) + 1
                st.session_state["failed_auth_attempts"] = fails
                if fails >= 5:
                    st.session_state["auth_lockout_until"] = time.time() + 900
                    st.error("🔒 Maximum authentication attempts exceeded. Terminal locked for 15 minutes.")
                else:
                    st.error(f"😕 Invalid credentials ({5 - fails} attempt(s) remaining before lockout).")
    return False

if not check_password():
    st.stop()

# ==============================================================================
# 4. CLOUD BACKUP (SUPABASE) — NEVER LETS OLD CLOUD DATA OVERWRITE NEWER LOCAL DATA
# ==============================================================================
@st.cache_resource
def get_supabase_client():
    if create_client is None:
        return None
    url = st.secrets.get("SUPABASE_URL") if hasattr(st, "secrets") else None
    key = st.secrets.get("SUPABASE_KEY") if hasattr(st, "secrets") else None
    if not url or not key:
        return None
    try:
        clean_url = url.strip().split("/rest/v1")[0].rstrip("/")
        return create_client(clean_url, key.strip())
    except Exception:
        st.session_state["db_error"] = "Supabase connection failed (Check secrets)."
        return None

supabase = get_supabase_client()

def record_db_error(context: str, err: Exception):
    raw_msg = str(err)
    sanitized = re.sub(r"https?://[^\s'\"]+", "[REDACTED_URL]", raw_msg)
    st.session_state["db_error"] = f"[{context}] {sanitized[:140]}"

def cloud_update(table: str, match: dict, payload: dict, label: str) -> bool:
    """Update the cloud copy; if it fails, queue it so it is retried (and hydrate can't undo it)."""
    if not supabase:
        return True
    try:
        q = supabase.table(table).update(payload)
        for k, v in match.items():
            q = q.eq(k, v)
        q.execute()
        return True
    except Exception as e:
        record_db_error(label, e)
        if "pending_cloud_sync" not in st.session_state:
            st.session_state["pending_cloud_sync"] = {}
        key = (table, tuple(sorted((str(k), str(v)) for k, v in match.items())))
        st.session_state["pending_cloud_sync"][key] = (table, dict(match), dict(payload))
        return False

def flush_pending_cloud_sync():
    pend = st.session_state.get("pending_cloud_sync", {})
    if not supabase or not pend:
        return
    for key, (table, match, payload) in list(pend.items()):
        try:
            q = supabase.table(table).update(payload)
            for k, v in match.items():
                q = q.eq(k, v)
            q.execute()
            del pend[key]
        except Exception as e:
            record_db_error("Retry Cloud Sync", e)
            break

def fetch_all_supabase_rows(table_name: str) -> list:
    if not supabase:
        return []
    all_rows = []
    chunk_size = 1000
    start = 0
    while True:
        try:
            res = supabase.table(table_name).select("*").range(start, start + chunk_size - 1).execute()
            if not res.data:
                break
            all_rows.extend(res.data)
            if len(res.data) < chunk_size:
                break
            start += chunk_size
        except Exception as e:
            record_db_error(f"Paginate {table_name}", e)
            break
    return all_rows

def _num(x, default=0.0):
    if x is None or (isinstance(x, str) and not x.strip()):
        return default
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def _int(x, default=0):
    if x is None or (isinstance(x, str) and not x.strip()):
        return default
    try:
        return int(float(x))
    except Exception:
        return default

def _norm_status(raw) -> str:
    if raw is None or str(raw).strip() == "":
        return "ACTIVE"
    return "ACTIVE" if classify_outcome(raw) == "ACTIVE" else str(raw).strip().upper()

def hydrate_duckdb_from_supabase():
    """Restore trades from the online backup. Each row is handled on its own, so ONE unreadable old row can never
    stop the newer rows (usually your open trades) from loading. Newer local data is never overwritten."""
    info = {"remote": 0, "remote_active": 0, "restored": 0, "kept_local": 0, "bad_rows": 0, "first_error": ""}
    st.session_state["hydrate_info"] = info
    if not supabase:
        return
    ist_now_str = ist_now().strftime('%Y-%m-%d %H:%M:%S')
    eq_data = fetch_all_supabase_rows("predictions")
    opt_data = fetch_all_supabase_rows("options_journal")
    info["remote"] = len(eq_data or [])
    info["remote_active"] = sum(1 for r in (eq_data or []) if _norm_status(r.get("status")) == "ACTIVE" and clean_sym_name(r.get("ticker", "")) and not str(r.get("ticker", "")).endswith(".L"))

    def bad(e):
        info["bad_rows"] += 1
        if not info["first_error"]:
            info["first_error"] = f"{type(e).__name__}: {str(e)[:80]}"

    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            local_eq, local_opt = {}, {}
            try:
                for r in con.execute("SELECT trade_id, status, last_audited, ticker, date_str, stop_loss, latest_price, pnl_pct FROM trade_journal").fetchall():
                    local_eq[r[0]] = r
                for r in con.execute("SELECT date_key, status, last_audited, current_option_price, pnl_pct FROM daily_options_journal").fetchall():
                    local_opt[r[0]] = r
            except Exception:
                pass

            for r in (eq_data or []):
                try:
                    ticker_val = clean_sym_name(r.get('ticker', ''))
                    if not ticker_val or ticker_val.endswith('.L'):
                        continue
                    pred_date = str(r.get('predicted_date') or ist_now_str[:10])[:10]
                    trade_id = f"{ticker_val}_{pred_date}"
                    remote_status = _norm_status(r.get('status'))
                    remote_ts = str(r.get('last_checked') or "")
                    loc = local_eq.get(trade_id)
                    if loc:
                        l_status, l_ts = str(loc[1]), str(loc[2] or "")
                        closed_vs_active = (l_status != "ACTIVE" and remote_status == "ACTIVE")
                        local_newer = bool(l_ts) and (not remote_ts or l_ts >= remote_ts)
                        if closed_vs_active or local_newer:
                            info["kept_local"] += 1
                            if closed_vs_active:
                                cloud_update("predictions", {"ticker": loc[3], "predicted_date": loc[4]},
                                             {"status": l_status, "stop_loss": _num(loc[5]), "latest_price": _num(loc[6]),
                                              "pnl_pct": _num(loc[7]), "last_checked": l_ts}, "Re-push Closed Trade")
                            continue
                    last_check = normalize_ts_str(r.get('last_checked'), f"{pred_date} 15:30:00")
                    entry = _num(r.get('entry_price'))
                    tgt, stp = _num(r.get('target_price')), _num(r.get('stop_loss'))
                    if remote_status == "ACTIVE" and (entry <= 0 or tgt <= 0 or stp <= 0 or tgt <= stp):
                        raise ValueError(f"{ticker_val}: missing or impossible prices (bought {r.get('entry_price')}, goal {r.get('target_price')}, exit {r.get('stop_loss')})")
                    shares_n = max(1, _int(r.get('shares_qty'), 1))
                    cap = _num(r.get('position_gbp'))
                    if cap <= 0:
                        cap = round(shares_n * entry, 2) if entry > 0 else POSITION_SIZE_INR
                    latest = _num(r.get('latest_price'), entry)
                    exit_p = latest if remote_status != "ACTIVE" else 0.0
                    con.execute("""
                        INSERT OR REPLACE INTO trade_journal 
                        (trade_id, timestamp, date_str, ticker, asset_type, entry_price, target_price,
                         stop_loss, shares, capital_allocated, status, latest_price, pnl_pct, exit_price,
                         exit_timestamp, last_audited, features_json)
                        VALUES (?, ?, ?, ?, 'EQUITY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [trade_id, last_check, pred_date, ticker_val, entry, tgt, stp,
                          shares_n, cap, remote_status, latest, _num(r.get('pnl_pct')), exit_p,
                          (last_check if remote_status != "ACTIVE" else None), last_check, json.dumps(_parse_features(r.get('features_json')))])
                    info["restored"] += 1
                except Exception as e:
                    bad(e)

            for o in (opt_data or []):
                try:
                    date_k = str(o.get('date_key') or ist_now_str[:10])[:10]
                    remote_status = _norm_status(o.get('status'))
                    remote_ts = str(o.get('last_audited') or "")
                    loc = local_opt.get(date_k)
                    if loc:
                        l_status, l_ts = str(loc[1]), str(loc[2] or "")
                        if (l_status != "ACTIVE" and remote_status == "ACTIVE") or (l_ts and (not remote_ts or l_ts >= remote_ts)):
                            if l_status != "ACTIVE" and remote_status == "ACTIVE":
                                cloud_update("options_journal", {"date_key": date_k},
                                             {"status": l_status, "current_option_price": _num(loc[3]), "pnl_pct": _num(loc[4]), "last_audited": l_ts}, "Re-push Closed Option")
                            continue
                    ts_str = normalize_ts_str(o.get('timestamp'), f"{date_k} 15:30:00")
                    aud_str = normalize_ts_str(o.get('last_audited') or o.get('timestamp'), ts_str)
                    cur_p = _num(o.get('current_option_price'))
                    if _num(o.get('entry_premium'), cur_p) <= 0 or not str(o.get('option_contract') or '').strip():
                        raise ValueError(f"option {date_k}: missing contract or price")
                    con.execute("""
                        INSERT OR REPLACE INTO daily_options_journal 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [date_k, ts_str, str(o.get('share_name') or 'ICICIBANK'), str(o.get('option_contract') or ''), _num(o.get('strike_price')),
                          _int(o.get('expiry_days'), 28), _num(o.get('underlying_spot')), _int(o.get('lot_size'), 700),
                          _num(o.get('entry_premium'), cur_p), cur_p, _num(o.get('target_premium')), _num(o.get('stop_loss_premium')),
                          _num(o.get('total_capital')), _num(o.get('ai_confidence'), 70.0), _num(o.get('implied_vol'), 20.0), remote_status,
                          _num(o.get('pnl_pct')), aud_str])
                except Exception as e:
                    bad(e)
        finally:
            con.close()
    if info["bad_rows"]:
        record_db_error("Restore from cloud", Exception(f"{info['bad_rows']} backup row(s) were unreadable and skipped ({info['first_error']})"))

if "hydrated_once" not in st.session_state:
    hydrate_duckdb_from_supabase()
    st.session_state["hydrated_once"] = True


# ==============================================================================
# 5. PRICE FEED (YAHOO V8 CHART) WITH PARALLEL PREFETCH
# ==============================================================================
def _fetch_history_raw(yf_sym: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    encoded_sym = urllib.parse.quote(str(yf_sym).strip(), safe="")
    hosts = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
    for idx, host in enumerate(hosts):
        try:
            url = f"{host}/v8/finance/chart/{encoded_sym}"
            params = {"range": period, "interval": interval, "includePrePost": "false"}
            headers = {
                "User-Agent": USER_AGENTS[idx % len(USER_AGENTS)],
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://finance.yahoo.com",
                "Referer": "https://finance.yahoo.com/"
            }
            resp = yf_session.get(url, params=params, headers=headers, timeout=4)
            if resp.status_code == 200:
                payload = resp.json()
                result_list = (payload.get("chart") or {}).get("result")
                if result_list:
                    res_obj = result_list[0]
                    timestamps = res_obj.get("timestamp") or []
                    quote_list = ((res_obj.get("indicators") or {}).get("quote") or [{}])
                    quote = quote_list[0] if quote_list else {}
                    if timestamps and "close" in quote:
                        idx_ist = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
                        df = pd.DataFrame({
                            "Open": quote.get("open", [None] * len(timestamps)),
                            "High": quote.get("high", [None] * len(timestamps)),
                            "Low": quote.get("low", [None] * len(timestamps)),
                            "Close": quote.get("close", [None] * len(timestamps)),
                            "Volume": quote.get("volume", [0] * len(timestamps)),
                        }, index=idx_ist)
                        df = df.dropna(subset=["Close"]).copy()
                        if not df.empty:
                            df["Open"] = df["Open"].fillna(df["Close"]).astype(float)
                            df["High"] = df["High"].fillna(df["Close"]).astype(float)
                            df["Low"] = df["Low"].fillna(df["Close"]).astype(float)
                            df["Close"] = df["Close"].astype(float)
                            df["Volume"] = df["Volume"].fillna(0).astype(float)
                            return df
        except Exception:
            pass
    return pd.DataFrame()

@st.cache_data(ttl=120, show_spinner=False)
def fetch_cached_history(yf_sym: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    return _fetch_history_raw(yf_sym, period, interval)

def prefetch_histories(syms: list, period: str = "6mo", ttl=120, workers: int = 6, progress=None, time_budget: float = None) -> bool:
    """Download price histories in parallel into a shared in-memory store.
    ttl can be a number or a function(symbol)->seconds. Returns False if the time budget ran out first."""
    store = get_hist_store()
    now = time.time()
    ttl_of = ttl if callable(ttl) else (lambda _s: ttl)
    with store["lock"]:
        for k in [k for k, v in store["data"].items() if now - v[0] > QUIET_TTL_SEC + 600]:
            del store["data"][k]
        todo = [s for s in dict.fromkeys(syms) if (s, period) not in store["data"] or now - store["data"][(s, period)][0] > ttl_of(s)]
    if not todo:
        if progress:
            progress(1, 1)
        return True
    started, done, complete = time.time(), 0, True
    batch_n = max(workers * 6, 24)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i in range(0, len(todo), batch_n):
            if time_budget is not None and time.time() - started > time_budget:
                complete = False
                break
            part = todo[i:i + batch_n]
            res = list(ex.map(lambda s: (s, _fetch_history_raw(s, period)), part))
            stamp = time.time()
            with store["lock"]:
                for s, df in res:
                    old = store["data"].get((s, period))
                    if df.empty and old is not None and not old[1].empty:
                        continue                                    # never replace good data with a failed download
                    store["data"][(s, period)] = (stamp, df)
            done += len(part)
            if progress:
                progress(done, len(todo))
            if len(part) >= 12 and sum(1 for _, d in res if d.empty) > len(part) * 0.6:
                time.sleep(1.5)                                     # the price service looks busy: back off politely
    return complete

def peek_history(yf_sym: str, period: str = "6mo", max_age: float = None) -> pd.DataFrame:
    """Prices already downloaded by prefetch_histories (no network). With max_age, older copies count as missing."""
    store = get_hist_store()
    with store["lock"]:
        item = store["data"].get((yf_sym, period))
    if item is None or item[1] is None or item[1].empty:
        return pd.DataFrame()
    if max_age is not None and time.time() - item[0] > max_age:
        return pd.DataFrame()
    return item[1].copy()

def get_history_fast(yf_sym: str, period: str = "6mo") -> pd.DataFrame:
    df = peek_history(yf_sym, period, max_age=SWEEP_TTL_SEC)
    if not df.empty:
        return df
    return fetch_cached_history(yf_sym, period, "1d")

def _bar_dates(h: pd.DataFrame) -> list:
    return [pd.Timestamp(x).date() for x in h.index]

# ==============================================================================
# 6. NEWS / FILINGS CHECK (WHOLE-WORD MATCHING) & MACRO
# ==============================================================================
@st.cache_data(ttl=900, show_spinner=False)
def evaluate_nse_filing_nlp(ticker: str) -> dict:
    clean_sym = clean_sym_name(ticker)
    headlines = []
    base_penalty = 1.0
    try:
        ann = check_corporate_announcements(clean_sym)
        if isinstance(ann, dict):
            base_penalty = float(ann.get("risk_penalty", 1.0))
            for k in ["headline", "title", "subject", "summary", "reason"]:
                if ann.get(k):
                    headlines.append(str(ann[k]))
    except Exception:
        pass

    combined_text = " | ".join(headlines).lower()
    lex_delta = 0.0
    matched_tags = []
    for pat, phrase, weight in _BEAR_PATTERNS:
        if pat.search(combined_text):
            lex_delta += weight
            matched_tags.append(f"🚨 {phrase.title()} ({weight:+.0f}%)")
    for pat, phrase, weight in _BULL_PATTERNS:
        if pat.search(combined_text):
            lex_delta += weight
            matched_tags.append(f"🚀 {phrase.title()} ({weight:+.0f}%)")

    if base_penalty < 0.85 and lex_delta == 0.0:
        lex_delta -= 15.0
        matched_tags.append("🚨 Adverse Corporate Event (-15%)")

    total_delta = round(max(-30.0, min(12.0, lex_delta)), 1)
    is_hard_blocked = (total_delta <= -15.0) or (base_penalty <= 0.75)

    if not ANNOUNCEMENTS_AVAILABLE:
        status = "⚪ News check unavailable"
    elif is_hard_blocked:
        status = "🚨 Bad news — blocked"
    elif total_delta >= 4.0:
        status = "🟢 Good news"
    else:
        status = "📰 No red flags"

    primary_headline = headlines[0][:120] if headlines else "No recent headline found"
    return {
        "status": status, "delta": total_delta,
        "nlp_tags": ", ".join(matched_tags) if matched_tags else "Nothing unusual",
        "headline": primary_headline, "is_blocked": is_hard_blocked, "checked": ANNOUNCEMENTS_AVAILABLE
    }

@st.cache_data(ttl=1800, show_spinner=False)
def get_indian_cross_asset_macro() -> dict:
    macro = {"nifty_5d": 0.0, "usdinr_5d": 0.0, "brent_5d": 0.0, "vix_level": 14.0}
    symbols = {"nifty_5d": "^NSEI", "usdinr_5d": "INR=X", "brent_5d": "BZ=F"}
    for key, sym in symbols.items():
        try:
            h = fetch_cached_history(sym, period="1mo", interval="1d")
            if not h.empty and len(h) >= 5:
                c_now = float(h["Close"].iloc[-1])
                c_5d = float(h["Close"].iloc[-5])
                if c_5d > 0:
                    macro[key] = round(((c_now - c_5d) / c_5d) * 100.0, 2)
        except Exception:
            pass
    try:
        h_vix = fetch_cached_history("^INDIAVIX", period="5d", interval="1d")
        if not h_vix.empty:
            macro["vix_level"] = round(float(h_vix["Close"].iloc[-1]), 1)
    except Exception:
        pass
    return macro

def is_market_stress(macro: dict) -> bool:
    return float(macro.get("nifty_5d", 0.0)) <= STRESS_NIFTY_5D or float(macro.get("vix_level", 14.0)) >= STRESS_VIX

def compute_nse_macro_lead_lag(ticker: str, macro: dict) -> tuple:
    t = clean_sym_name(ticker).upper()
    delta = 0.0
    notes = []
    brent = macro.get("brent_5d", 0.0)
    usdinr = macro.get("usdinr_5d", 0.0)
    nifty = macro.get("nifty_5d", 0.0)

    if t in INR_IT_PHARMA_EXPORTERS and usdinr >= 0.4:
        delta += 2.5
        notes.append(f"+2.5% (weaker rupee helps exporters, +{usdinr:.1f}%)")
    if t in CRUDE_SENSITIVE_USERS and brent >= 3.0:
        delta -= 3.0
        notes.append(f"-3.0% (oil up +{brent:.1f}% hurts costs)")
    elif t in CRUDE_SENSITIVE_USERS and brent <= -3.0:
        delta += 2.5
        notes.append(f"+2.5% (oil down {brent:.1f}% helps costs)")
    if nifty <= -2.0:
        delta -= 2.0
        notes.append(f"-2.0% (Nifty down {nifty:.1f}% this week)")
    elif nifty >= 1.5 and delta == 0:
        delta += 1.5
        notes.append(f"+1.5% (Nifty up +{nifty:.1f}% this week)")
    return round(max(-5.0, min(5.0, delta)), 1), (" | ".join(notes) if notes else "Nothing unusual in the markets")

# ==============================================================================
# 7. AI MODEL, CALIBRATION, MEMORY & GOVERNED RETRAINING
# ==============================================================================
def get_ml_model():
    if "ml_model_bundle" in st.session_state:
        return st.session_state["ml_model_bundle"]
    for path in (LIVE_MODEL_PATH, MODEL_PATH):
        if os.path.exists(path):
            try:
                bundle = joblib.load(path)
                st.session_state["ml_model_bundle"] = bundle
                st.session_state["ml_model_source"] = "Upgraded model" if path == LIVE_MODEL_PATH else "Original model"
                return bundle
            except Exception:
                continue
    return None

def ensemble_proba(bundle, X: pd.DataFrame):
    """Average win probability from whatever model format is stored. Returns an array (one value per row)."""
    parts = []
    if isinstance(bundle, dict):
        for k in ("lgb", "xgb"):
            if k in bundle:
                parts.append(np.asarray(bundle[k].predict_proba(X))[:, 1])
        if not parts and "model" in bundle:
            parts.append(np.asarray(bundle["model"].predict_proba(X))[:, 1])
    else:
        parts.append(np.asarray(bundle.predict_proba(X))[:, 1])
    if not parts:
        raise ValueError("unrecognised model format")
    return np.mean(parts, axis=0)

def load_closed_trades_df() -> pd.DataFrame:
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            return con.execute("""
                SELECT trade_id, ticker, date_str, exit_timestamp, last_audited, status, pnl_pct,
                       entry_price, capital_allocated, features_json
                FROM trade_journal WHERE status != 'ACTIVE'
            """).df()
        except Exception:
            return pd.DataFrame()
        finally:
            con.close()

def get_closed_loop_self_learning(ticker: str, current_atr_pct: float, hist=None) -> dict:
    """Exponentially decayed memory of past wins/losses on this stock (+ a caution for very jumpy shares)."""
    delta = 0.0
    reasons = []
    today_dt = ist_now().date()
    if hist is None:
        hist = load_closed_trades_df()
    if hist is None or hist.empty:
        return {"delta": 0.0, "reason": "Nothing learned yet"}
    try:
        t_clean = clean_sym_name(ticker)
        t_hist = hist[hist["ticker"].apply(clean_sym_name) == t_clean]
        decayed_loss = decayed_win = 0.0
        for _, r in t_hist.iterrows():
            d = _to_date(_first_valid(r.get("exit_timestamp"), r.get("last_audited"), r.get("date_str")))
            days_ago = max(0, (today_dt - d).days) if d else 15
            weight = math.pow(0.5, days_ago / HALF_LIFE_DAYS)
            o = classify_outcome(r["status"])
            if o == "LOSS":
                decayed_loss += 15.0 * weight
            elif o == "WIN":
                decayed_win += 5.0 * weight
        if decayed_loss >= 1.0:
            delta -= round(decayed_loss, 1)
            reasons.append(f"-{decayed_loss:.1f}% (lost on this stock recently)")
        if decayed_win >= 1.0:
            delta += round(decayed_win, 1)
            reasons.append(f"+{decayed_win:.1f}% (won on this stock recently)")
        losses = hist[hist["status"].astype(str).str.contains("LOSS", na=False)]
        loss_atrs = []
        for f_str in losses["features_json"].dropna():
            f_obj = _parse_features(f_str)
            a = f_obj.get("atr_pct", (f_obj.get("raw_features") or {}).get("atr_pct"))
            if a is not None:
                try:
                    loss_atrs.append(float(a))
                except Exception:
                    pass
        if len(loss_atrs) >= 5:
            p75 = max(3.0, float(np.percentile(loss_atrs, 75)))
            if current_atr_pct >= p75:
                delta -= 8.0
                reasons.append(f"-8% (very jumpy — losing trades were this jumpy, {p75:.1f}%+ a day)")
    except Exception:
        pass
    return {"delta": round(delta, 1), "reason": " | ".join(reasons) if reasons else "No past lesson applies"}

def build_calibrator(closed_df: pd.DataFrame):
    """Turns the model's raw probability into an honest one using real results."""
    wins = losses = 0
    xs, ys = [], []
    if closed_df is not None and not closed_df.empty:
        for _, r in closed_df.iterrows():
            o = classify_outcome(r["status"])
            if o == "WIN":
                y = 1
            elif o == "LOSS":
                y = 0
            else:
                continue
            wins += y
            losses += (1 - y)
            f = _parse_features(r["features_json"])
            rp = f.get("raw_prob")
            if rp is None and f.get("raw_ml_prob") is not None:
                rp = float(f["raw_ml_prob"]) / 100.0
            if rp is not None:
                try:
                    xs.append(float(rp))
                    ys.append(y)
                except Exception:
                    pass
    decided = wins + losses
    prior = min(0.75, max(0.35, wins / decided)) if decided >= 10 else 0.5
    iso = None
    mode = f"Shrunk toward the real win rate ({prior:.0%}, {decided} trades)"
    if len(xs) >= MIN_CALIBRATION_SAMPLES and len(set(ys)) == 2:
        try:
            from sklearn.isotonic import IsotonicRegression
            iso = IsotonicRegression(out_of_bounds="clip").fit(xs, ys)
            mode = f"Tuned on {len(xs)} real results"
        except Exception:
            iso = None

    def calibrate(p):
        base = prior + CONF_SHRINK * (float(p) - 0.5)
        if iso is not None:
            try:
                base = 0.5 * float(iso.predict([float(p)])[0]) + 0.5 * base
            except Exception:
                pass
        return min(0.95, max(0.05, base))

    return calibrate, {"mode": mode, "prior": prior, "decided": decided}

def check_and_auto_retrain_model(feature_cols: list, closed_df: pd.DataFrame):
    """Champion/challenger: a new model is trained on live trades, tested on the most recent 30%, and only
    replaces the old one (saved to a SEPARATE file) if it is clearly better. Your original file is never touched."""
    if closed_df is None or closed_df.empty:
        return
    records = []
    for _, r in closed_df.iterrows():
        o = classify_outcome(r["status"])
        if o not in ("WIN", "LOSS"):
            continue
        raw = _parse_features(r["features_json"]).get("raw_features", {})
        if not isinstance(raw, dict) or not all(c in raw for c in feature_cols):
            continue
        try:
            vec = [float(raw[c]) for c in feature_cols]
        except Exception:
            continue
        records.append((str(r.get("date_str") or ""), vec, 1 if o == "WIN" else 0))
    n = len(records)
    if n < MIN_SETTLED_TO_RETRAIN:
        return
    try:
        state = json.loads(get_meta("retrain_state", "{}") or "{}")
    except Exception:
        state = {}
    today_s = ist_now().strftime("%Y-%m-%d")
    if state.get("last_attempt_date") == today_s or n - int(state.get("last_labeled_count", 0)) < RETRAIN_STEP_INTERVAL:
        return
    records.sort(key=lambda x: x[0])
    X_all = pd.DataFrame([r[1] for r in records], columns=feature_cols)
    y_all = np.array([r[2] for r in records])
    split = int(n * 0.7)
    X_tr, X_te, y_tr, y_te = X_all.iloc[:split], X_all.iloc[split:], y_all[:split], y_all[split:]
    state.update({"last_attempt_date": today_s, "last_labeled_count": n})
    if len(np.unique(y_tr)) < 2 or len(np.unique(y_te)) < 2:
        set_meta("retrain_state", json.dumps(state))
        return
    try:
        from lightgbm import LGBMClassifier
        from xgboost import XGBClassifier
        from sklearn.metrics import log_loss, roc_auc_score

        def fit_pair(X, y):
            m1 = LGBMClassifier(n_estimators=250, learning_rate=0.03, max_depth=5, min_child_samples=10, random_state=42, verbose=-1)
            m1.fit(X, y)
            m2 = XGBClassifier(n_estimators=300, learning_rate=0.03, max_depth=5, random_state=42, eval_metric="logloss")
            m2.fit(X, y)
            return m1, m2

        c_l, c_x = fit_pair(X_tr, y_tr)
        p_ch = np.clip(0.5 * c_l.predict_proba(X_te)[:, 1] + 0.5 * c_x.predict_proba(X_te)[:, 1], 1e-4, 1 - 1e-4)
        champ = get_ml_model()
        if champ is None:
            set_meta("retrain_state", json.dumps(state))
            return
        p_cm = np.clip(ensemble_proba(champ, X_te), 1e-4, 1 - 1e-4)
        ll_ch, ll_cm = float(log_loss(y_te, p_ch)), float(log_loss(y_te, p_cm))
        auc_ch, auc_cm = float(roc_auc_score(y_te, p_ch)), float(roc_auc_score(y_te, p_cm))
        if (ll_ch < ll_cm * 0.98) and (auc_ch >= auc_cm):
            f_l, f_x = fit_pair(X_all, y_all)
            new_bundle = {"lgb": f_l, "xgb": f_x, "type": "ensemble", "feature_cols": feature_cols}
            os.makedirs(os.path.dirname(LIVE_MODEL_PATH), exist_ok=True)
            joblib.dump(new_bundle, LIVE_MODEL_PATH)
            st.session_state["ml_model_bundle"] = new_bundle
            st.session_state["ml_model_source"] = "Upgraded model"
            state["last_promoted"] = ist_now().strftime("%Y-%m-%d %H:%M")
            st.session_state["retrain_notice"] = "🧠 The AI upgraded itself: the new version tested better than the old one."
        else:
            st.session_state["retrain_notice"] = "🧪 The AI tested a new version but kept the old one (the new one wasn't better)."
        set_meta("retrain_state", json.dumps(state))
    except Exception as e:
        set_meta("retrain_state", json.dumps(state))
        record_db_error("AI upgrade check", e)

# ==============================================================================
# 8. OPTIONS ENGINE (LIVE NSE QUOTE WHEN AVAILABLE, OTHERWISE A CLEARLY-LABELLED MODEL PRICE)
# ==============================================================================
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_black_scholes_call(spot: float, strike: float, days_to_exp: float, r: float, sigma: float) -> float:
    T = max(days_to_exp, 1.0) / 365.0
    if spot <= 0 or strike <= 0 or sigma <= 0:
        return max(0.0, spot - strike)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    call_price = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)
    return max(round(call_price, 2), 0.05)

def model_option_price(spot, strike, days, sigma, side: str) -> float:
    mid = calculate_black_scholes_call(spot, strike, days, RISK_FREE_RATE, sigma)
    if side == "ask":
        return round(mid * (1.0 + OPTION_MODEL_HALF_SPREAD), 2)
    return max(round(mid * (1.0 - OPTION_MODEL_HALF_SPREAD), 2), 0.05)

def get_nse_monthly_expiry(current_date: datetime) -> tuple:
    """NSE stock-option monthly expiry = LAST TUESDAY of the month (changed from Thursday in Sept 2025).
    Holidays aren't known here; if the exchange moves the date earlier, the contract is simply marked at expiry."""
    def last_tuesday_of(y, m):
        cal = calendar.monthcalendar(y, m)
        tuesdays = [week[1] for week in cal if week[1] != 0]
        return datetime(y, m, tuesdays[-1], 15, 30)
    year, month = current_date.year, current_date.month
    expiry_dt = last_tuesday_of(year, month)
    days_left = (expiry_dt.date() - current_date.date()).days
    if days_left < 5:
        month2 = month + 1 if month < 12 else 1
        year2 = year if month < 12 else year + 1
        expiry_dt = last_tuesday_of(year2, month2)
        days_left = (expiry_dt.date() - current_date.date()).days
    return expiry_dt, max(1, days_left)

def _nse_get_json(url: str):
    box = get_nse_http_box()
    with box["lock"]:
        now = time.time()
        if box["session"] is None or now - box["ts"] > 600:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENTS[0], "Accept": "application/json,text/plain,*/*",
                              "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/option-chain"})
            try:
                s.get("https://www.nseindia.com", timeout=5)      # NSE needs this handshake to hand out cookies
            except Exception:
                pass
            box["session"], box["ts"] = s, now
        s = box["session"]
    try:
        r = s.get(url, timeout=6)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# ---------- public-holiday awareness ----------
def get_nse_holidays() -> dict:
    """{'YYYY-MM-DD': 'Holiday name'} from NSE's own trading-holiday list. Cached; retried every 30 min if NSE didn't answer."""
    box = get_nse_http_box()
    now = time.time()
    cached = box.get("holidays")
    if cached and (now - cached[0] < (43200 if cached[1] else 1800)):
        return cached[1]
    out = {}
    data = _nse_get_json("https://www.nseindia.com/api/holiday-master?type=trading")
    try:
        for seg in ("CM", "FO"):
            for item in (data or {}).get(seg, []) or []:
                d = datetime.strptime(str(item.get("tradingDate")), "%d-%b-%Y").date()
                out[d.isoformat()] = str(item.get("description", "Market holiday"))[:60]
            if out:
                break
    except Exception:
        out = {}
    box["holidays"] = (now, out)
    return out

def get_market_state() -> dict:
    """Is today a trading day? Uses weekends, NSE's holiday list, and (if the list is unavailable) a live check that the
    Nifty has a price bar dated today. If every check fails we assume a normal trading day."""
    now = ist_now()
    today = now.date()
    if now.weekday() > 4:
        return {"trading_day": False, "reason": "Weekend"}
    hol = get_nse_holidays()
    if today.isoformat() in hol:
        return {"trading_day": False, "reason": f"Market holiday — {hol[today.isoformat()]}"}
    if not hol and dtime(9, 45) <= now.time() <= dtime(15, 30):
        try:
            h = fetch_cached_history("^NSEI", period="5d", interval="1d")
            if not h.empty and _bar_dates(h)[-1] != today:
                return {"trading_day": False, "reason": "No trading data today — probably a market holiday"}
        except Exception:
            pass
    return {"trading_day": True, "reason": ""}

def next_open_text() -> str:
    now = ist_now()
    hol = get_nse_holidays()
    for add in range(0, 12):
        d = now.date() + timedelta(days=add)
        if d.weekday() > 4 or d.isoformat() in hol:
            continue
        if add == 0 and now.time() >= dtime(15, 30):
            continue
        if add == 0 and now.time() >= dtime(9, 15):
            return "now"
        when = "today" if add == 0 else ("tomorrow" if add == 1 else d.strftime("%a %d %b"))
        return f"{when} 09:15 IST"
    return "soon"

def upcoming_holidays(n: int = 4) -> list:
    today = ist_now().date().isoformat()
    return [(d, name) for d, name in sorted(get_nse_holidays().items()) if d >= today][:n]

def get_nse_option_quote(symbol: str, expiry_dt: datetime, strike: float):
    """Real bid/ask for ONE contract (right expiry, right strike) or None if NSE doesn't answer or the quote is poor."""
    data = _nse_get_json(f"https://www.nseindia.com/api/option-chain-equities?symbol={urllib.parse.quote(clean_sym_name(symbol), safe='')}")
    if not data:
        return None
    want = expiry_dt.strftime("%d-%b-%Y")
    try:
        for item in (data.get("records") or {}).get("data", []):
            if str(item.get("expiryDate")) == want and abs(float(item.get("strikePrice", 0)) - float(strike)) < 1e-6:
                ce = item.get("CE") or {}
                bid, ask = float(ce.get("bidprice") or 0), float(ce.get("askPrice") or 0)
                if not (bid > 0 and ask > 0 and ask >= bid):
                    return None
                spread = (ask - bid) / ((ask + bid) / 2.0) * 100.0
                oi = float(ce.get("openInterest") or 0)
                iv = float(ce.get("impliedVolatility") or 0)
                return {"bid": bid, "ask": ask, "oi": oi, "iv": iv, "spread_pct": spread}
    except Exception:
        return None
    return None

def is_live_option(contract) -> bool:
    return "(live quote)" in str(contract)

def generate_daily_options_alpha() -> dict:
    now_ist = ist_now()
    today_str = now_ist.strftime('%Y-%m-%d')
    now_str = now_ist.strftime('%Y-%m-%d %H:%M:%S')
    st.session_state["opt_note"] = ""

    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            df = con.execute("SELECT * FROM daily_options_journal WHERE date_key = ?", [today_str]).df()
            if not df.empty:
                return df.iloc[0].to_dict()
            active_opt_shares = set(con.execute("SELECT DISTINCT share_name FROM daily_options_journal WHERE status = 'ACTIVE'").df()["share_name"].tolist())
        except Exception:
            active_opt_shares = set()
        finally:
            con.close()

    if not is_entry_window_open():
        st.session_state["opt_note"] = "The options idea appears during market hours (09:30–15:00 IST, Mon–Fri), so it uses a real price."
        return {}

    cooldown_set = get_recent_cooldown_tickers()
    learn = options_learning()
    skipped = {"rest": 0, "bar": 0, "live_only": 0}
    prefetch_histories(FNO_STOCKS, "1mo")
    ranked = []
    for sym in FNO_STOCKS:
        clean_name = clean_sym_name(sym)
        if clean_name in active_opt_shares or clean_name in cooldown_set:
            continue
        if clean_name in learn["rest"]:
            skipped["rest"] += 1                                # the options learner is resting this share
            continue
        if evaluate_nse_filing_nlp(clean_name).get("is_blocked", False):
            continue
        h = get_history_fast(sym, "1mo")
        if h.empty or len(h) < 15 or _bar_dates(h)[-1] != now_ist.date():
            continue
        spot = float(h["Close"].iloc[-1])
        ret_5d = spot / float(h["Close"].iloc[-5]) - 1.0
        rets = np.log(h["Close"] / h["Close"].shift(1)).dropna()
        sigma = max(0.14, min(0.45, float(rets.std() * np.sqrt(252)))) if len(rets) > 2 else 0.25
        ranked.append((ret_5d, clean_name, spot, sigma))
    ranked.sort(reverse=True)

    expiry_dt, days_to_expiry = get_nse_monthly_expiry(now_ist)
    for ret_5d, name, spot, sigma in ranked:
        score = round(min(92.0, max(60.0, 70.0 + ret_5d * 150.0)), 1)
        if score < learn["bar"]:
            skipped["bar"] += 1                                 # not a strong enough climb for the learner's current bar
            continue
        lot = LOT_SIZES.get(name, 500)
        step = STRIKE_STEPS.get(name, 10)
        strike = max(step, math.ceil((spot * 1.02) / step) * step)
        quote = get_nse_option_quote(name, expiry_dt, strike)
        if quote and quote["spread_pct"] <= OPTION_MAX_SPREAD_PCT and quote["oi"] >= OPTION_MIN_OPEN_INTEREST:
            entry, mark, iv_pct, live = quote["ask"], quote["bid"], (quote["iv"] if quote["iv"] > 0 else sigma * 100.0), True
        else:
            entry = model_option_price(spot, strike, days_to_expiry, sigma, "ask")
            mark = model_option_price(spot, strike, days_to_expiry, sigma, "bid")
            iv_pct, live = sigma * 100.0, False
        if learn["live_only"] and not live:
            skipped["live_only"] += 1                           # the learner only trusts real NSE quotes right now
            continue
        total_cap = round(entry * lot, 2)
        if total_cap > OPTION_BUDGET_INR:
            continue
        label = f"{name} {expiry_dt.strftime('%d %b %Y').upper()} {int(strike)} CE ({'live quote' if live else 'model price'})"
        sig = {
            "date_key": today_str, "timestamp": now_str, "share_name": name, "option_contract": label,
            "strike_price": float(strike), "expiry_days": int(days_to_expiry), "underlying_spot": float(spot), "lot_size": int(lot),
            "entry_premium": float(entry), "current_option_price": float(mark),
            "target_premium": round(entry * OPTION_TARGET_MULT, 2), "stop_loss_premium": round(entry * OPTION_STOP_MULT, 2),
            "total_capital": float(total_cap), "ai_confidence": score, "implied_vol": round(float(iv_pct), 1),
            "status": "ACTIVE", "pnl_pct": round((mark - entry) / entry * 100.0, 2), "last_audited": now_str
        }
        with DB_LOCK:
            con = duckdb.connect(DB_PATH, read_only=False)
            try:
                con.execute("""
                    INSERT OR REPLACE INTO daily_options_journal 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                """, [sig[k] for k in ["date_key", "timestamp", "share_name", "option_contract", "strike_price", "expiry_days", "underlying_spot",
                                       "lot_size", "entry_premium", "current_option_price", "target_premium", "stop_loss_premium", "total_capital",
                                       "ai_confidence", "implied_vol", "pnl_pct", "last_audited"]])
            finally:
                con.close()
        if supabase:
            try:
                c_check = supabase.table("options_journal").select("date_key").eq("date_key", today_str).execute()
                if not c_check.data:
                    supabase.table("options_journal").insert(sig).execute()
            except Exception as e:
                record_db_error("Insert Option", e)
        return sig

    why = []
    if skipped["rest"]:
        why.append(f"{skipped['rest']} share(s) are resting after a recent option loss")
    if skipped["bar"]:
        why.append(f"{skipped['bar']} didn't climb enough for the learner's current bar (trend strength {learn['bar']:.0f}+)")
    if skipped["live_only"]:
        why.append(f"{skipped['live_only']} had only an estimated price, and the learner currently wants a real NSE quote")
    st.session_state["opt_note"] = ("No option idea today — the options learner is being picky: " + "; ".join(why) + ". Doing nothing is a valid choice."
                                    if why else f"No option idea today: none of the F&O shares had a recent live price with a contract under ₹{OPTION_BUDGET_INR:,.0f}.")
    return {}

def audit_option(opt, now_str: str, today_date):
    """Live-quote options are re-quoted on NSE; model-price options are re-priced with the SAME volatility they started with."""
    name = str(opt["share_name"])
    strike = float(opt["strike_price"])
    entry_prem = float(opt["entry_premium"])
    target_prem = float(opt["target_premium"])
    stop_prem = float(opt["stop_loss_premium"])
    sigma = float(opt["implied_vol"]) / 100.0
    opt_dt = _to_date(opt["date_key"]) or today_date
    expiry_dt = datetime.combine(opt_dt, dtime(15, 30)) + timedelta(days=int(opt["expiry_days"]))
    elapsed = max(0, (today_date - opt_dt).days)
    last_price = float(opt["current_option_price"])
    live_src = is_live_option(opt["option_contract"])

    h = fetch_cached_history(f"{name}.NS", period="1mo", interval="1d")
    if h.empty:
        st.session_state["price_feed_problem"] = True
        return
    dates = _bar_dates(h)
    start_after = max(opt_dt, _to_date(opt.get("last_audited")) or opt_dt)
    status, live = "ACTIVE", last_price

    def prem_at(spot, d, side="bid"):
        rem = max(1, (expiry_dt.date() - d).days)
        return model_option_price(spot, strike, rem, sigma, side)

    if live_src:
        if is_nse_market_open():
            q = get_nse_option_quote(name, expiry_dt, strike)
            if q:
                live = q["bid"]
                if live >= target_prem:
                    status = "🎯 WIN (TARGET HIT)"
                elif live <= stop_prem:
                    status = "🛑 LOSS (STOPPED OUT)"
    else:
        for i, d in enumerate(dates):
            if d <= start_after or d >= today_date:
                continue
            row = h.iloc[i]
            if prem_at(float(row["Low"]), d) <= stop_prem:
                status, live = "🛑 LOSS (STOPPED OUT)", stop_prem
                break
            if prem_at(float(row["High"]), d) >= target_prem:
                status, live = "🎯 WIN (TARGET HIT)", target_prem
                break
        if status == "ACTIVE":
            spot = float(h["Close"].iloc[-1])
            live = prem_at(spot, today_date)
            if live >= target_prem:
                status, live = "🎯 WIN (TARGET HIT)", target_prem
            elif live <= stop_prem:
                status, live = "🛑 LOSS (STOPPED OUT)", stop_prem

    if status == "ACTIVE" and today_date >= expiry_dt.date():
        status = "⏱️ EXPIRED (TIME EXIT)"
        live = max(0.0, float(h["Close"].iloc[-1]) - strike)          # value at expiry = intrinsic value
    elif status == "ACTIVE" and elapsed >= MAX_HOLD_CALENDAR_DAYS:
        status = "⏱️ EXPIRED (TIME EXIT)"

    pnl_pct = round(((live - entry_prem) / entry_prem) * 100.0, 2) if entry_prem > 0 else 0.0
    spot_now = float(h["Close"].iloc[-1])
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            con.execute("""
                UPDATE daily_options_journal
                SET current_option_price = ?, underlying_spot = ?, pnl_pct = ?, status = ?, last_audited = ?
                WHERE date_key = ?
            """, [live, spot_now, pnl_pct, status, now_str, opt["date_key"]])
        finally:
            con.close()
    cloud_update("options_journal", {"date_key": opt["date_key"]},
                 {"current_option_price": live, "underlying_spot": spot_now, "pnl_pct": pnl_pct, "status": status, "last_audited": now_str},
                 "Audit Option")


# ---------- the OPTIONS learner: simple, visible rules that adapt to its own option results ----------
def load_options_journal_df() -> pd.DataFrame:
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            return con.execute("SELECT * FROM daily_options_journal ORDER BY date_key ASC").df()
        except Exception:
            return pd.DataFrame()
        finally:
            con.close()

def _opt_group(g: pd.DataFrame):
    if g is None or g.empty:
        return None
    return {"n": int(len(g)), "avg": round(float(g["pnl_pct"].mean()), 1),
            "win_rate": round(float((g["outcome"] == "WIN").mean() * 100.0), 0),
            "wavg": round(float((g["w"] * g["pnl_pct"]).sum() / max(1e-9, g["w"].sum())), 1)}

def options_learning(df_all: pd.DataFrame = None) -> dict:
    """Learns ONLY from finished option ideas and ONLY changes the options idea:
       1) a share whose option lost recently takes a break from options (fades like the share memory: half every 30 days),
       2) after enough results, if recent ideas lose on average, it only takes shares with a stronger 5-day climb,
       3) if estimated-price ideas keep losing, it only takes ideas with a real NSE quote.
       Rules 2 and 3 look at the last 60 days only, so if a rule stops all ideas it lifts by itself and gets re-tested."""
    out = {"n": 0, "recent_n": 0, "ready": False, "bar": 60.0, "rest": {}, "live_only": False, "wavg": None,
           "live": None, "model": None, "by_share": {}}
    if df_all is None:
        df_all = load_options_journal_df()
    d = prep_perf_frame(df_all, "total_capital", is_option=True)
    if d is None or d.empty:
        return out
    today = ist_now().date()
    d = d.copy()
    d["age"] = d["exit_date"].apply(lambda x: max(0, (today - x).days) if x else 0)
    d["w"] = 0.5 ** (d["age"] / HALF_LIFE_DAYS)
    d["live_src"] = d["option_contract"].apply(is_live_option)
    out["n"] = int(len(d))
    out["live"], out["model"] = _opt_group(d[d["live_src"]]), _opt_group(d[~d["live_src"]])
    for share, g in d.groupby("share_name"):
        out["by_share"][str(share)] = _opt_group(g)
        sc = 0.0
        for _, r in g.iterrows():
            o = r["outcome"]
            v = 1.0 if o == "WIN" else (-1.0 if o == "LOSS" else (-0.5 if float(r["pnl_pct"]) < 0 else 0.0))
            sc += float(r["w"]) * v
        if sc <= -OPT_REST_THRESHOLD:
            out["rest"][str(share)] = max(1, int(math.ceil(HALF_LIFE_DAYS * math.log2(abs(sc) / OPT_REST_THRESHOLD))))
    recent = d[d["age"] <= OPT_LEARN_WINDOW_DAYS]
    out["recent_n"] = int(len(recent))
    if len(recent) >= OPT_LEARN_MIN_TRADES:
        out["ready"] = True
        wavg = float((recent["w"] * recent["pnl_pct"]).sum() / max(1e-9, recent["w"].sum()))
        out["wavg"] = round(wavg, 1)
        if wavg < 0:
            out["bar"] = round(min(OPT_MAX_TREND_BAR, 60.0 + abs(wavg) * 0.5), 1)
    rm, rl = _opt_group(recent[~recent["live_src"]]), _opt_group(recent[recent["live_src"]])
    if rm and rm["n"] >= OPT_LIVE_ONLY_MIN_MODEL and rm["wavg"] < 0 and (rl is None or rl["n"] < 5 or rl["wavg"] > rm["wavg"]):
        out["live_only"] = True
    return out

def trend_bar_to_rise(bar: float) -> float:
    """Trend strength = 70 + 5-day rise(%) × 1.5  ->  the 5-day rise (%) a share needs to reach a given bar."""
    return (float(bar) - 70.0) / 1.5

# ==============================================================================
# 9. AUDIT ENGINE (BAR-BY-BAR, GAP-AWARE, STOP-FIRST, HONEST FILLS)
# ==============================================================================
def audit_and_reconcile_all_trades():
    now_ist = ist_now()
    now_str = now_ist.strftime('%Y-%m-%d %H:%M:%S')
    today_date = now_ist.date()
    st.session_state["price_feed_problem"] = False
    flush_pending_cloud_sync()

    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            active_trades = con.execute("SELECT * FROM trade_journal WHERE status = 'ACTIVE'").df()
            active_opts = con.execute("SELECT * FROM daily_options_journal WHERE status = 'ACTIVE'").df()
        except Exception:
            active_trades, active_opts = pd.DataFrame(), pd.DataFrame()
        finally:
            con.close()

    got_any_price = False
    for _, tr in (active_trades.iterrows() if not active_trades.empty else []):
        tkr = str(tr["ticker"]).strip()
        try:
            h = fetch_cached_history(normalize_ticker_for_yf(tkr), period="1mo", interval="1d")
            if h.empty:
                st.session_state["price_feed_problem"] = True
                continue                                       # do NOT pretend we checked
            got_any_price = True
            dates = _bar_dates(h)
            entry, target, stop = float(tr["entry_price"]), float(tr["target_price"]), float(tr["stop_loss"])
            friction = get_trade_friction_pct(tr.get("features_json"))
            be_floor = get_breakeven_exit_price(entry, friction)
            target_dist = target - entry
            be_trigger = entry + BREAKEVEN_TRIGGER_RATIO * target_dist
            entry_dt = _to_date(tr["date_str"])
            if entry_dt is None:
                continue
            start_after = max(entry_dt, _to_date(tr.get("last_audited")) or entry_dt)

            new_status, exit_price, exit_ts = "ACTIVE", 0.0, None
            curr = float(h["Close"].iloc[-1])

            # 1) completed days we haven't looked at yet (covers days the app was asleep)
            for i, d in enumerate(dates):
                if d <= start_after or d >= today_date:
                    continue
                row = h.iloc[i]
                o, hi, lo, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
                if not all(math.isfinite(v) for v in (o, hi, lo, c)):
                    continue
                outcome, fill, new_stop = evaluate_completed_bar(o, hi, lo, c, entry, target, stop, be_floor, be_trigger)
                if outcome == "WIN":
                    new_status, exit_price = "🎯 WIN (TARGET HIT)", fill
                elif outcome == "STOP":
                    new_status, exit_price = classify_stop_exit(entry, stop, fill, friction), fill
                else:
                    stop, curr = new_stop, c
                if outcome:
                    exit_ts = f"{d} 15:30:00"
                    break

            # 2) today's live bar (entry-day guard: pre-entry highs/lows never count)
            if new_status == "ACTIVE" and dates and dates[-1] == today_date:
                last = h.iloc[-1]
                curr = float(last["Close"])
                day_high, day_low = float(last["High"]), float(last["Low"])
                same_day = (entry_dt == today_date)
                if target_dist > 0 and stop < be_floor:
                    ratchet_hit = (curr >= be_trigger) if same_day else (curr >= be_trigger or day_high >= be_trigger)
                    if ratchet_hit and curr > be_floor:        # never ratchet above where the price actually is
                        stop = be_floor
                is_be = is_stop_breakeven_protected(entry, stop)
                hit_target = (curr >= target) if same_day else (curr >= target or day_high >= target)
                hit_stop = (curr <= stop) if (same_day or is_be) else (curr <= stop or day_low <= stop)
                if hit_stop:                                   # stop wins ties (conservative)
                    fill = curr if curr <= stop else stop
                    new_status, exit_price = classify_stop_exit(entry, stop, fill, friction), fill
                elif hit_target:
                    new_status, exit_price = "🎯 WIN (TARGET HIT)", target

            # 3) time exit
            if new_status == "ACTIVE" and (today_date - entry_dt).days >= MAX_HOLD_CALENDAR_DAYS:
                new_status, exit_price = "⏱️ EXPIRED (TIME EXIT)", curr

            mark = exit_price if new_status != "ACTIVE" else curr
            pnl = calc_net_equity_pnl_pct(entry, mark, friction)
            with DB_LOCK:
                con = duckdb.connect(DB_PATH, read_only=False)
                try:
                    con.execute("""
                        UPDATE trade_journal
                        SET latest_price = ?, stop_loss = ?, pnl_pct = ?, status = ?, exit_price = ?,
                            exit_timestamp = CASE WHEN ? != 'ACTIVE' THEN ? ELSE exit_timestamp END,
                            last_audited = ?
                        WHERE trade_id = ?
                    """, [mark, stop, pnl, new_status, exit_price, new_status, exit_ts or now_str, now_str, tr["trade_id"]])
                finally:
                    con.close()
            cloud_update("predictions", {"ticker": tkr, "predicted_date": tr["date_str"]},
                         {"status": new_status, "stop_loss": stop, "latest_price": mark, "pnl_pct": pnl, "last_checked": now_str}, "Audit Equity")
        except Exception:
            continue

    for _, opt in (active_opts.iterrows() if not active_opts.empty else []):
        try:
            audit_option(opt, now_str, today_date)
            got_any_price = True
        except Exception:
            continue

    if got_any_price or (active_trades.empty and active_opts.empty):
        st.session_state["last_price_check"] = now_ist.strftime("%d %b %H:%M")

# ==============================================================================
# 10. LOGGING & GUARDRAILS
# ==============================================================================
def get_active_positions_df() -> pd.DataFrame:
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            return con.execute("SELECT ticker, entry_price, stop_loss, capital_allocated, features_json FROM trade_journal WHERE status = 'ACTIVE'").df()
        except Exception:
            return pd.DataFrame()
        finally:
            con.close()

def get_currently_active_tickers() -> set:
    df = get_active_positions_df()
    return set(clean_sym_name(t) for t in df["ticker"].tolist()) if not df.empty else set()

def get_active_sector_exposure() -> dict:
    counts = {}
    df = get_active_positions_df()
    for _, r in (df.iterrows() if not df.empty else []):
        sec = get_trade_sector(r["ticker"], r["features_json"])
        counts[sec] = counts.get(sec, 0) + 1
    return counts

def get_portfolio_capacity() -> dict:
    df = get_active_positions_df()
    n_open, risk = len(df), 0.0
    for _, r in (df.iterrows() if not df.empty else []):
        try:
            e, s, cap = float(r["entry_price"]), float(r["stop_loss"]), float(r["capital_allocated"])
            if e > 0 and s < e:
                risk += cap * (e - s) / e
        except Exception:
            pass
    reason = ""
    if n_open >= MAX_OPEN_POSITIONS:
        reason = f"You already have the maximum number of open trades ({n_open}/{MAX_OPEN_POSITIONS})"
    elif risk >= MAX_OPEN_RISK_INR:
        reason = f"Too much money is at risk already (₹{risk:,.0f} of ₹{MAX_OPEN_RISK_INR:,.0f})"
    return {"open": n_open, "risk_inr": round(risk, 2), "ok": reason == "", "reason": reason}

def get_todays_logged_equities() -> pd.DataFrame:
    today_str = ist_now().strftime('%Y-%m-%d')
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            return con.execute("SELECT * FROM trade_journal WHERE date_str = ? ORDER BY timestamp ASC, ticker ASC", [today_str]).df()
        except Exception:
            return pd.DataFrame()
        finally:
            con.close()

def get_recent_cooldown_tickers() -> set:
    today_dt = ist_now().date()
    cooldown = set()
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            eq_closed = con.execute("SELECT ticker, COALESCE(exit_timestamp, last_audited, date_str) AS close_dt FROM trade_journal WHERE status != 'ACTIVE'").df()
            opt_closed = con.execute("SELECT share_name AS ticker, COALESCE(last_audited, date_key) AS close_dt FROM daily_options_journal WHERE status != 'ACTIVE'").df()
            for _, r in pd.concat([eq_closed, opt_closed], ignore_index=True).iterrows():
                c_dt = _to_date(r["close_dt"])
                if c_dt and 0 <= (today_dt - c_dt).days <= COOLDOWN_CALENDAR_DAYS:
                    cooldown.add(clean_sym_name(r["ticker"]))
        except Exception:
            pass
        finally:
            con.close()
    return cooldown

def log_equity_signal_safely(sig: dict, enforce_sector_cap: bool = True) -> bool:
    now = ist_now()
    today_str = now.strftime('%Y-%m-%d')
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    ticker = clean_sym_name(sig.get('Ticker', ''))

    def fail(msg):
        st.session_state["last_log_reason"] = msg
        return False

    if not ticker:
        return fail("Invalid stock")
    if not is_entry_window_open():
        return fail("It is outside buying hours (09:30–15:00 IST, Mon–Fri)")
    if not sig.get("Loggable", True):
        return fail("No live price from today yet (holiday or data delay)")
    if is_market_stress(get_indian_cross_asset_macro()):
        return fail("Buying is paused because the market is under stress (Nifty falling or India VIX high)")
    cap_state = get_portfolio_capacity()
    if not cap_state["ok"]:
        return fail(cap_state["reason"])
    sector = str(sig.get('Sector') or get_ticker_sector(ticker))
    if enforce_sector_cap and get_active_sector_exposure().get(sector, 0) >= sector_cap(sector):
        return fail(f"You already hold the maximum in {sector}")

    f_dict = sig.get('FeaturesDict', {})
    f_json_str = json.dumps(f_dict)
    init_net_pnl = -get_trade_friction_pct(f_json_str)
    logged_local = False
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            cnt = con.execute("SELECT COUNT(*) AS cnt FROM trade_journal WHERE date_str = ?", [today_str]).df()
            if not cnt.empty and int(cnt["cnt"].iloc[0]) >= MAX_DAILY_EQUITY_TRADES:
                return fail("All of today's picks are already used")
            existing = con.execute("SELECT trade_id FROM trade_journal WHERE ticker = ? AND (status = 'ACTIVE' OR date_str = ?)", [ticker, today_str]).df()
            if not existing.empty:
                return fail("You already hold this stock")
            shares_num = int(str(sig['Recommended Shares']).split()[0])
            con.execute("""
                INSERT INTO trade_journal 
                (trade_id, timestamp, date_str, ticker, asset_type, entry_price, target_price,
                 stop_loss, shares, capital_allocated, status, latest_price, pnl_pct, exit_price,
                 exit_timestamp, last_audited, features_json)
                VALUES (?, ?, ?, ?, 'EQUITY', ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 0.0, NULL, ?, ?)
            """, [f"{ticker}_{today_str}", now_str, today_str, ticker, float(sig['Price (₹)']), float(sig['Target (₹)']), float(sig['Stop Loss (₹)']),
                  shares_num, float(sig['RawCapital']), float(sig['Price (₹)']), init_net_pnl, now_str, f_json_str])
            logged_local = True
        except Exception as e:
            record_db_error("Log trade", e)
        finally:
            con.close()

    if supabase and logged_local:
        try:
            cloud_exist = supabase.table("predictions").select("id").eq("ticker", ticker).eq("predicted_date", today_str).execute()
            if not cloud_exist.data:
                supabase.table("predictions").insert({
                    "predicted_date": today_str, "ticker": ticker, "company_name": f"{ticker} ({sector})",
                    "entry_price": float(sig['Price (₹)']), "target_price": float(sig['Target (₹)']), "stop_loss": float(sig['Stop Loss (₹)']),
                    "position_gbp": float(sig['RawCapital']), "shares_qty": int(str(sig['Recommended Shares']).split()[0]),
                    "profit_goal": float(sig['ReturnNum']), "confidence": int(float(str(sig['Adjusted Score']))), "hold_days": 5,
                    "status": "ACTIVE", "latest_price": float(sig['Price (₹)']), "pnl_pct": init_net_pnl,
                    "news_status": sig.get("FilingStatus", "Clean"), "rns_headline": str(sig.get("FilingHeadline", "Active AI Quant Signal"))[:120],
                    "features_json": f_dict, "last_checked": now_str
                }).execute()
        except Exception as e:
            record_db_error("Insert Equity", e)
    return logged_local

# ==============================================================================
# 11. FEATURES & THE SCAN
# ==============================================================================
def calculate_technical_features(df_hist: pd.DataFrame, deliv_shock: float = 1.0) -> pd.DataFrame:
    """Built from COMPLETED daily bars only."""
    if df_hist.empty or len(df_hist) < 20:
        return pd.DataFrame()
    df = pd.DataFrame()
    df['close'] = df_hist['Close'].astype(float).replace(0.0, np.nan)
    df['volume'] = df_hist['Volume'].astype(float)
    df = df.dropna(subset=['close'])
    if len(df) < 20:
        return pd.DataFrame()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean().replace(0.0, np.nan)
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean().replace(0.0, np.nan)
    high = df_hist['High'].astype(float)
    low = df_hist['Low'].astype(float)
    prev_close = df['close'].shift()
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df['atr_14'] = tr.rolling(14).mean()
    df['dist_ema20_pct'] = ((df['close'] - df['ema20']) / df['ema20']) * 100.0
    df['trend_spread_pct'] = ((df['ema20'] - df['ema50']) / df['ema50']) * 100.0
    df['atr_pct'] = (df['atr_14'] / df['close']) * 100.0
    vol_20 = df['volume'].rolling(20).mean().replace(0.0, np.nan)
    df['rvol'] = (df['volume'] / vol_20).fillna(1.0).clip(lower=0.5, upper=5.0)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss.replace(0.0, np.nan)
    df['rsi_14'] = 100.0 - (100.0 / (1.0 + rs.fillna(1.0)))
    df['deliv_shock'] = float(deliv_shock)
    return df.replace([np.inf, -np.inf], np.nan).dropna()

def analyse_share(clean_sym: str, df_hist: pd.DataFrame, today_date, market_open: bool):
    """Cheap checks every share gets. Returns (code, info); code is one of no_data / illiquid / wild / ok."""
    if df_hist is None or df_hist.empty:
        return "no_data", None
    dates = _bar_dates(df_hist)
    live_bar_today = (dates[-1] == today_date)              # also catches NSE holidays
    partial_bar = live_bar_today and market_open
    live_price = float(df_hist["Close"].iloc[-1])
    df_model = df_hist.iloc[:-1] if partial_bar else df_hist
    if len(df_model) < 30 or not np.isfinite(live_price) or live_price <= 0:
        return "no_data", None
    recent = df_model.tail(20)
    avg_turnover = float((recent["Close"] * recent["Volume"]).mean())
    if (not math.isfinite(avg_turnover)) or live_price < MIN_PRICE_INR or avg_turnover < MIN_AVG_TURNOVER_INR:
        return "illiquid", None
    last10 = df_model.tail(10)
    frozen_days = int(((last10["High"] - last10["Low"]).abs() <= last10["Close"].abs() * 0.0005).sum())
    if frozen_days >= 3:
        return "wild", None                                 # stuck at its price limit (circuit) on several days
    df_feat = calculate_technical_features(df_model, deliv_shock=1.15)
    if df_feat.empty or len(df_feat) < 20:
        return "no_data", None
    latest = df_feat.iloc[-1]
    model_close = float(latest["close"])
    ema20 = float(latest["ema20"])
    atr = max(float(latest.get("atr_14", model_close * 0.02)), model_close * 0.012)
    atr_pct = round((atr / live_price) * 100.0, 2)
    if float(latest["atr_pct"]) > MAX_ATR_PCT:
        return "wild", None
    feat = {
        "dist_ema20_pct": round(float(latest["dist_ema20_pct"]), 2),
        "trend_spread_pct": round(float(latest["trend_spread_pct"]), 2),
        "atr_pct": round(float(latest["atr_pct"]), 2),
        "rvol": round(float(latest["rvol"]), 2),
        "rsi_14": round(float(latest["rsi_14"]), 1),
        "deliv_shock": round(max(1.0, min(2.5, float(latest["rvol"]) * 1.1)), 2)   # proxy: no delivery data feed
    }
    closes = df_model["Close"]
    ret20 = float(closes.iloc[-1] / closes.iloc[-21] - 1.0) * 100.0 if len(closes) > 21 else 0.0
    return "ok", {
        "dates": dates, "live_bar_today": live_bar_today, "live_price": live_price, "avg_turnover": avg_turnover,
        "latest": latest, "model_close": model_close, "ema20": ema20, "atr": atr, "atr_pct": atr_pct, "feat": feat,
        "ret20": round(ret20, 2),
        "is_above_trend": (model_close >= ema20) and (feat["rsi_14"] >= 48.0) and (feat["trend_spread_pct"] >= -1.0),
        "is_exhausted": (feat["rsi_14"] > 78.0) or (feat["dist_ema20_pct"] > 8.5),
        "has_volume": feat["rvol"] >= MIN_RVOL_THRESHOLD,
    }

def _score_share(ml_bundle, feature_cols, calibrate, info):
    raw_feature_map = {c: float(info["feat"].get(c, info["latest"].get(c, 0.0))) for c in feature_cols}
    raw_prob = float(ensemble_proba(ml_bundle, pd.DataFrame([raw_feature_map], columns=feature_cols))[0])
    if not math.isfinite(raw_prob):
        raise ValueError("model returned a non-number")
    raw_prob = max(0.01, min(0.99, raw_prob))
    base_conf = round(min(95.0, max(5.0, calibrate(raw_prob) * 100.0)), 1)
    return raw_feature_map, raw_prob, base_conf

def _nifty_20d_return() -> float:
    try:
        h = fetch_cached_history("^NSEI", period="3mo", interval="1d")
        c = h["Close"]
        return float(c.iloc[-1] / c.iloc[-21] - 1.0) * 100.0 if len(c) > 21 else 0.0
    except Exception:
        return 0.0

def run_predictions():
    """Looks at EVERY share in NSE's normal (EQ) series:
       1) download prices for all of them (in parallel; quiet shares are re-checked less often),
       2) cheap filters for everyone: tradable, not stuck at price limits, uptrend, not overbought, normal volume,
       3) AI score for the survivors, 4) the best finalists get a fresh price + the full news/memory/market check."""
    stats = {"listed": 0, "priced": 0, "no_data": 0, "illiquid": 0, "wild": 0, "scanned": 0, "uptrend": 0,
             "not_uptrend": 0, "overbought": 0, "quiet": 0, "held": 0, "low_score": 0, "deep": 0, "qualified": 0,
             "errors": 0, "stale": 0, "logged": 0, "paused": False, "window_open": is_entry_window_open(), "error": "",
             "source": "", "partial": False, "sector_breadth": {}, "leaders": [], "seconds": 0.0}
    t_start = time.time()
    ml_bundle = get_ml_model()
    if ml_bundle is None:
        stats["error"] = "The AI model file wasn't found, so picks are paused (nothing is guessed)."
        st.session_state["scan_stats"] = stats
        return pd.DataFrame(), False
    feature_cols = ml_bundle.get("feature_cols", FEATURE_COLS) if isinstance(ml_bundle, dict) else FEATURE_COLS

    closed_df = load_closed_trades_df()
    check_and_auto_retrain_model(feature_cols, closed_df)
    ml_bundle = get_ml_model() or ml_bundle
    feature_cols = ml_bundle.get("feature_cols", FEATURE_COLS) if isinstance(ml_bundle, dict) else FEATURE_COLS
    calibrate, cal_info = build_calibrator(closed_df)
    st.session_state["cal_info"] = cal_info

    today_date = ist_now().date()
    market_open = is_nse_market_open()
    window_open = is_entry_window_open()
    macro = get_indian_cross_asset_macro()
    paused = is_market_stress(macro)
    stats["paused"] = paused

    symbols, source = get_all_nse_symbols()
    stats["listed"], stats["source"] = len(symbols), source
    get_nse_industry_map()                                   # warm the sector labels once
    active_held = get_currently_active_tickers()
    sector_counts = get_active_sector_exposure()
    cooldown = get_recent_cooldown_tickers()

    box = get_nse_http_box()
    quiet_seen = box.setdefault("quiet_seen", {})          # share -> when it was last seen thinly traded
    ttl_for = lambda ys: QUIET_TTL_SEC if (time.time() - quiet_seen.get(ys, 0.0)) < QUIET_TTL_SEC else SWEEP_TTL_SEC
    yf_syms = [f"{c}.NS" for c in symbols]
    prog = st.progress(0, text=f"Downloading prices for {len(yf_syms):,} NSE shares...")
    def _tick(done, total):
        try:
            prog.progress(min(1.0, done / max(1, total)), text=f"Downloading prices: {done:,} of {total:,} still needed")
        except Exception:
            pass
    complete = prefetch_histories(yf_syms, "6mo", ttl=ttl_for, workers=SCAN_WORKERS, progress=_tick, time_budget=SCAN_TIME_BUDGET_SEC)
    stats["partial"] = not complete

    nifty20 = _nifty_20d_return()
    breadth, candidates, leaders = {}, [], []
    for clean_sym in symbols:
        ys = f"{clean_sym}.NS"
        df_hist = peek_history(ys, "6mo")
        if df_hist.empty:
            stats["no_data"] += 1
            continue
        stats["priced"] += 1
        try:
            code, info = analyse_share(clean_sym, df_hist, today_date, market_open)
        except Exception:
            stats["errors"] += 1
            continue
        if code != "ok":
            stats[code] += 1
            if code == "illiquid":
                quiet_seen[ys] = time.time()
            continue
        quiet_seen.pop(ys, None)
        stats["scanned"] += 1
        sec = get_ticker_sector(clean_sym)
        b = breadth.setdefault(sec, [0, 0])
        b[1] += 1
        if info["is_above_trend"]:
            stats["uptrend"] += 1
            b[0] += 1
            leaders.append((round(info["ret20"] - nifty20, 2), clean_sym, sec))
        if not info["is_above_trend"]:
            stats["not_uptrend"] += 1
            continue
        if info["is_exhausted"]:
            stats["overbought"] += 1
            continue
        if not info["has_volume"]:
            stats["quiet"] += 1
            continue
        if clean_sym in active_held or clean_sym in cooldown or sector_counts.get(sec, 0) >= sector_cap(sec):
            stats["held"] += 1
            continue
        try:
            _, _, base_conf = _score_share(ml_bundle, feature_cols, calibrate, info)
        except Exception:
            stats["errors"] += 1                            # no guessing when the model can't score a share
            continue
        if base_conf < MIN_CALIBRATED_CONFIDENCE - DEEP_CHECK_MARGIN:
            stats["low_score"] += 1
            continue
        candidates.append((base_conf, clean_sym))
    stats["sector_breadth"] = {k: (v[0], v[1]) for k, v in breadth.items()}
    stats["leaders"] = [(t, sec, rs) for rs, t, sec in sorted(leaders, reverse=True)[:8]]
    stats["nifty_20d"] = round(nifty20, 2)

    candidates.sort(key=lambda x: (-x[0], x[1]))
    stats["candidates"] = len(candidates)
    finalists = [c for _, c in candidates[:MAX_DEEP_CHECKS]]
    stats["low_score"] += max(0, len(candidates) - len(finalists))
    if finalists:
        prog.progress(1.0, text=f"Double-checking the best {len(finalists)} shares with fresh prices...")
        prefetch_histories([f"{c}.NS" for c in finalists], "6mo", ttl=FRESH_PRICE_TTL_SEC, workers=6)

    results = []
    for clean_sym in finalists:
        try:
            df_hist = peek_history(f"{clean_sym}.NS", "6mo", max_age=FRESH_PRICE_MAX_AGE_SEC)
            if df_hist.empty:
                stats["stale"] += 1                         # no fresh price -> never buy on an old one
                continue
            code, info = analyse_share(clean_sym, df_hist, today_date, market_open)
            if code != "ok":
                stats[code] += 1
                continue
            try:
                raw_feature_map, raw_prob, base_conf = _score_share(ml_bundle, feature_cols, calibrate, info)
            except Exception:
                stats["errors"] += 1
                continue
            live_price, feat, atr, atr_pct, dates = info["live_price"], info["feat"], info["atr"], info["atr_pct"], info["dates"]
            avg_turnover = info["avg_turnover"]
            is_above_trend, is_exhausted, has_volume = info["is_above_trend"], info["is_exhausted"], info["has_volume"]

            nlp = evaluate_nse_filing_nlp(clean_sym)
            learner = get_closed_loop_self_learning(clean_sym, atr_pct, closed_df)
            macro_delta, macro_note = compute_nse_macro_lead_lag(clean_sym, macro)
            trend_penalty = 0.0 if is_above_trend else -10.0
            total_adj = round(learner["delta"] + macro_delta + nlp["delta"] + trend_penalty, 1)
            final_score = round(min(98.0, max(5.0, base_conf + total_adj)), 1)

            target = round(live_price + 1.6 * atr, 2)
            stop = round(live_price - 1.1 * atr, 2)
            shares = max(1, int(POSITION_SIZE_INR / live_price))
            capital = round(shares * live_price, 2)
            slip = slippage_pct_per_side(avg_turnover)
            friction = compute_costs_pct(capital, slip)
            net_return = round(((target - live_price) / live_price) * 100.0 - friction, 2)
            sector = get_ticker_sector(clean_sym)
            rel_strength = round(info["ret20"] - nifty20, 2)

            qualified = (is_above_trend and not is_exhausted and has_volume and not nlp["is_blocked"]
                         and final_score >= MIN_CALIBRATED_CONFIDENCE and net_return >= MIN_NET_RETURN_PCT)
            if nlp["is_blocked"]:
                reason = f"Bad news flagged: {nlp['nlp_tags']}"
            elif not is_above_trend:
                reason = "Not in an uptrend any more (fresh price)"
            elif is_exhausted:
                reason = "Already run up too far (overbought)"
            elif not has_volume:
                reason = f"Too little trading today (below {MIN_RVOL_THRESHOLD}× normal volume)"
            elif final_score < MIN_CALIBRATED_CONFIDENCE:
                reason = f"AI confidence {final_score:.0f}% is below the {MIN_CALIBRATED_CONFIDENCE:.0f}% needed"
            elif net_return < MIN_NET_RETURN_PCT:
                reason = f"Possible profit after costs is only {net_return:+.2f}%"
            else:
                reason = "Passed every check"

            snapshot = {
                "raw_features": raw_feature_map, "atr_pct": atr_pct, "raw_prob": round(raw_prob, 4), "base_ml_conf": base_conf,
                "learner_delta": learner["delta"], "macro_delta": macro_delta, "nlp_delta": nlp["delta"], "sector": sector,
                "friction_pct": friction, "avg_turnover_inr": round(avg_turnover, 0), "entry_bar": str(dates[-1]), "final_score": final_score,
                "rel_strength_20d": rel_strength
            }
            results.append({
                "Ticker": clean_sym, "Sector": sector, "Price (₹)": round(live_price, 2),
                "Expected Return": f"+{net_return}% after costs", "ReturnNum": net_return,
                "Recommended Shares": f"{shares} shares", "RawCapital": capital, "Total Cost (₹)": f"₹{capital:,.0f}",
                "Target (₹)": target, "Stop Loss (₹)": stop, "Costs %": friction,
                "AI Win Confidence": f"{base_conf}%", "Base": base_conf, "Adjustment": total_adj,
                "Why": f"{learner['reason']} | {macro_note}", "FilingStatus": nlp["status"], "FilingTags": nlp["nlp_tags"],
                "FilingHeadline": nlp["headline"], "Adjusted Score": final_score, "Qualified": qualified, "Rejection Reason": reason,
                "Easy to trade (₹cr/day)": round(avg_turnover / 1e7, 1), "Loggable": bool(info["live_bar_today"] and window_open),
                "Vs Nifty (20d)": rel_strength, "FeaturesDict": snapshot, "FullSymbol": f"{clean_sym}.NS"
            })
        except Exception:
            stats["errors"] += 1
    prog.empty()
    stats["deep"] = len(results)
    stats["seconds"] = round(time.time() - t_start, 1)

    if not results:
        st.session_state["scan_stats"] = stats
        return pd.DataFrame(), False
    df_sorted = pd.DataFrame(results).sort_values(by=["Qualified", "Adjusted Score", "ReturnNum"], ascending=[False, False, False]).reset_index(drop=True)
    qualified_only = df_sorted[df_sorted["Qualified"] == True]
    stats["qualified"] = int(len(qualified_only))
    if not qualified_only.empty and not paused:
        for _, sig in qualified_only.iterrows():
            if not bool(sig.get("Loggable", False)):
                continue
            if len(get_todays_logged_equities()) >= MAX_DAILY_EQUITY_TRADES:
                break
            if log_equity_signal_safely(sig.to_dict(), enforce_sector_cap=True):
                stats["logged"] += 1
    st.session_state["scan_stats"] = stats
    return df_sorted, not qualified_only.empty

# ==============================================================================
# 12. PERFORMANCE MATHS
# ==============================================================================
def prep_perf_frame(df: pd.DataFrame, cap_col: str, is_option: bool = False) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df[df["status"].apply(classify_outcome) != "ACTIVE"].copy()
    if d.empty:
        return d
    d["cap"] = pd.to_numeric(d[cap_col], errors="coerce").fillna(POSITION_SIZE_INR)
    d["pnl_pct"] = pd.to_numeric(d["pnl_pct"], errors="coerce").fillna(0.0)
    d["pnl_inr"] = d["cap"] * d["pnl_pct"] / 100.0
    d["outcome"] = d["status"].apply(classify_outcome)
    if is_option:
        d["entry_date"] = d["date_key"].apply(_to_date)
        d["exit_date"] = d["last_audited"].apply(_to_date)
    else:
        d["entry_date"] = d["date_str"].apply(_to_date)
        d["exit_date"] = d.apply(lambda r: _to_date(_first_valid(r.get("exit_timestamp"), r.get("last_audited"), r.get("date_str"))), axis=1)
    d["exit_date"] = d.apply(lambda r: r["exit_date"] or r["entry_date"], axis=1)
    return d.sort_values(["exit_date", "entry_date"], kind="stable").reset_index(drop=True)

def compute_performance(d: pd.DataFrame) -> dict:
    n = len(d)
    if n == 0:
        return {"n": 0}
    wins, losses = int((d["outcome"] == "WIN").sum()), int((d["outcome"] == "LOSS").sum())
    be, exp = int((d["outcome"] == "BE").sum()), int((d["outcome"] == "EXPIRED").sum())
    pos, neg = d.loc[d["pnl_inr"] > 0, "pnl_inr"], d.loc[d["pnl_inr"] < 0, "pnl_inr"]
    curve = np.array([0.0] + d["pnl_inr"].cumsum().tolist())
    lo, hi = wilson_interval(wins, n)
    return {
        "n": n, "wins": wins, "losses": losses, "be": be, "expired": exp,
        "target_hit_rate": wins / n * 100.0, "ci_low": lo * 100.0, "ci_high": hi * 100.0,
        "avg_win": float(pos.mean()) if len(pos) else 0.0, "avg_loss": float(neg.mean()) if len(neg) else 0.0,
        "expectancy_pct": float(d["pnl_pct"].mean()), "expectancy_inr": float(d["pnl_inr"].mean()),
        "profit_factor": (float(pos.sum()) / abs(float(neg.sum()))) if len(neg) and neg.sum() != 0 else None,
        "max_dd": float((curve - np.maximum.accumulate(curve)).min()), "net_inr": float(d["pnl_inr"].sum()),
    }

@st.cache_data(ttl=1800, show_spinner=False)
def get_nifty_close_series() -> pd.Series:
    try:
        h = fetch_cached_history("^NSEI", period="6mo", interval="1d")
        if h is None or h.empty:
            return pd.Series(dtype=float)
        s = h["Close"].copy()
        s.index = pd.to_datetime([pd.Timestamp(x).date() for x in s.index])
        return s.sort_index()
    except Exception:
        return pd.Series(dtype=float)

def nifty_return_between(series: pd.Series, d0, d1):
    try:
        if series is None or series.empty or d0 is None or d1 is None:
            return None
        a, b = series.asof(pd.Timestamp(d0)), series.asof(pd.Timestamp(d1))
        if pd.isna(a) or pd.isna(b) or float(a) <= 0:
            return None
        return (float(b) / float(a) - 1.0) * 100.0
    except Exception:
        return None

# ==============================================================================
# 13. LOOK & FEEL
# ==============================================================================
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{--a1:#f97316;--a2:#16a34a;--good:#16a34a;--bad:#dc2626;--warn:#d97706;--info:#2563eb;--line:rgba(128,128,128,.28);--soft:rgba(128,128,128,.08);}
html,body,.stApp{font-family:'Inter',system-ui,-apple-system,sans-serif;}
.block-container{padding-top:1.1rem;max-width:1450px;}
footer{visibility:hidden;}
@keyframes drift{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
.hero{border:1px solid var(--line);border-radius:22px;padding:22px 26px;background:linear-gradient(120deg,rgba(249,115,22,.26),rgba(250,204,21,.10),rgba(22,163,74,.22));background-size:200% 200%;animation:drift 16s ease infinite;margin-bottom:12px;}
.hero h1{margin:0;font-size:1.9rem;font-weight:800;letter-spacing:-.03em;background:linear-gradient(90deg,#f97316,#16a34a);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.hero .sub{opacity:.85;font-size:.95rem;margin-top:6px;}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;}
.chip{border:1px solid var(--line);background:var(--soft);padding:4px 12px;border-radius:999px;font-size:.78rem;font-weight:600;white-space:nowrap;}
.chip.up{color:var(--good);border-color:rgba(22,163,74,.5);}
.chip.down{color:var(--bad);border-color:rgba(220,38,38,.5);}
.chip.warn{color:var(--warn);border-color:rgba(217,119,6,.55);}
.sec{display:flex;align-items:center;gap:10px;margin:20px 0 10px;}
.sec .bar{width:5px;height:28px;border-radius:4px;background:linear-gradient(var(--a1),var(--a2));}
.sec h3{margin:0;font-size:1.25rem;font-weight:800;letter-spacing:-.01em;}
.sec small{opacity:.65;margin-left:8px;font-size:.82rem;font-weight:500;}
.score{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:6px 0 14px;}
.sc{position:relative;border:1px solid var(--line);border-radius:18px;padding:18px 20px;background:var(--soft);overflow:hidden;transition:transform .18s ease,box-shadow .18s ease;}
.sc:hover{transform:translateY(-3px);box-shadow:0 12px 30px rgba(0,0,0,.16);}
.sc:before{content:"";position:absolute;left:0;top:0;right:0;height:4px;background:linear-gradient(90deg,var(--a1),#facc15);}
.sc.good:before{background:linear-gradient(90deg,#16a34a,#4ade80);}
.sc.bad:before{background:linear-gradient(90deg,#dc2626,#f87171);}
.sc .lbl{font-size:.72rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;opacity:.65;}
.sc .val{font-size:2.2rem;font-weight:800;letter-spacing:-.03em;line-height:1.15;margin-top:6px;}
.sc .sub{font-size:.8rem;opacity:.72;margin-top:4px;}
.pos{color:var(--good);}
.neg{color:var(--bad);}
.callout{border-radius:16px;padding:14px 18px;margin:8px 0 14px;border:1px solid var(--line);font-size:.95rem;display:flex;gap:12px;align-items:flex-start;}
.callout .ic{font-size:1.4rem;line-height:1.25;}
.callout.good{background:rgba(22,163,74,.12);border-color:rgba(22,163,74,.45);}
.callout.bad{background:rgba(220,38,38,.12);border-color:rgba(220,38,38,.45);}
.callout.warn{background:rgba(217,119,6,.13);border-color:rgba(217,119,6,.45);}
.callout.info{background:rgba(37,99,235,.10);border-color:rgba(37,99,235,.40);}
.colh{display:flex;justify-content:space-between;align-items:center;border-radius:14px;padding:10px 14px;margin-bottom:10px;font-weight:800;border:1px solid var(--line);}
.colh.buy{background:linear-gradient(90deg,rgba(249,115,22,.22),rgba(249,115,22,.05));}
.colh.run{background:linear-gradient(90deg,rgba(22,163,74,.22),rgba(22,163,74,.05));}
.colh.done{background:linear-gradient(90deg,rgba(100,116,139,.25),rgba(100,116,139,.05));}
.colh span.n{font-size:.8rem;opacity:.8;font-weight:700;}
.tcard,.mini,.stat,.lesson,.fin,.empty{border:1px solid var(--line);border-radius:16px;padding:14px 16px;background:var(--soft);margin-bottom:10px;}
.tcard,.mini{transition:transform .15s ease,box-shadow .15s ease;}
.tcard:hover,.mini:hover{transform:translateY(-3px);box-shadow:0 10px 28px rgba(0,0,0,.18);}
.mini{padding:12px 14px;}
.mini.opt{border-left:4px solid var(--info);}
.fin{padding:10px 14px;display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;}
.empty{text-align:center;opacity:.75;font-size:.9rem;padding:22px 16px;border-style:dashed;}
.tag{font-size:.66rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:3px 9px;border-radius:999px;border:1px solid var(--line);}
.tag.good{color:var(--good);border-color:rgba(22,163,74,.5);}
.tag.bad{color:var(--bad);border-color:rgba(220,38,38,.5);}
.tag.warn{color:var(--warn);border-color:rgba(217,119,6,.5);}
.tag.info{color:var(--info);border-color:rgba(37,99,235,.5);}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;}
.tk{font-size:1.4rem;font-weight:800;letter-spacing:-.01em;}
.mtk{font-size:1.05rem;font-weight:800;}
.big{font-size:1.6rem;font-weight:800;line-height:1.1;}
.mp{font-size:1.05rem;font-weight:800;}
.sub2{font-size:.78rem;opacity:.72;}
.rbar{position:relative;height:10px;border-radius:999px;background:linear-gradient(90deg,rgba(220,38,38,.55),rgba(217,119,6,.45),rgba(22,163,74,.6));margin:16px 0 4px;}
.rbar.c{margin:12px 0 4px;height:8px;}
.rdot{position:absolute;top:-4px;width:18px;height:18px;border-radius:50%;background:#fff;border:3px solid var(--a1);transform:translateX(-50%);box-shadow:0 2px 8px rgba(0,0,0,.35);}
.rbar.c .rdot{width:16px;height:16px;}
.rtick{position:absolute;top:-3px;width:2px;height:16px;background:rgba(128,128,128,.9);transform:translateX(-50%);}
.rlab{display:flex;justify-content:space-between;font-size:.72rem;opacity:.75;gap:6px;}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;margin-top:10px;font-size:.8rem;}
.grid2 b{opacity:.6;font-weight:600;margin-right:4px;}
.ringwrap{position:relative;width:60px;height:60px;flex:none;}
.ring{width:60px;height:60px;border-radius:50%;-webkit-mask:radial-gradient(farthest-side,transparent 70%,#000 72%);mask:radial-gradient(farthest-side,transparent 70%,#000 72%);}
.ringnum{position:absolute;inset:0;display:grid;place-items:center;font-weight:800;font-size:.9rem;}
.note{font-size:.74rem;opacity:.75;margin-top:8px;}
.stat .l{font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;opacity:.6;}
.stat .v{font-size:1.2rem;font-weight:800;margin-top:4px;}
.hbars{display:flex;flex-direction:column;gap:10px;margin:6px 0 10px;}
.hb{display:grid;grid-template-columns:150px 1fr 120px;gap:10px;align-items:center;font-size:.86rem;}
.hb .t{height:12px;border-radius:999px;background:rgba(128,128,128,.18);overflow:hidden;}
.hb .f{height:100%;border-radius:999px;}
.hb .f.pos{background:linear-gradient(90deg,#16a34a,#4ade80);}
.hb .f.neg{background:linear-gradient(90deg,#dc2626,#f87171);}
.meter{height:16px;border-radius:999px;background:rgba(128,128,128,.2);overflow:hidden;margin:8px 0 4px;}
.meter>div{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--a1),var(--a2));}
.lesson{padding:12px 16px;margin-bottom:8px;font-size:.9rem;}
.funnel{display:flex;flex-direction:column;gap:7px;margin:6px 0 12px;}
.fn{display:grid;grid-template-columns:210px 1fr 80px;gap:10px;align-items:center;font-size:.86rem;}
.fn .t{height:22px;border-radius:8px;background:rgba(128,128,128,.12);overflow:hidden;}
.fn .f{height:100%;border-radius:8px;background:linear-gradient(90deg,var(--a1),var(--a2));opacity:.9;}
.fn .f.last{background:linear-gradient(90deg,#16a34a,#4ade80);}
.fn .c{text-align:right;font-weight:800;font-variant-numeric:tabular-nums;}
.gauge{height:14px;border-radius:999px;background:linear-gradient(90deg,rgba(220,38,38,.6),rgba(217,119,6,.5) 50%,rgba(22,163,74,.65));position:relative;margin:12px 0 6px;}
.gauge .needle{position:absolute;top:-5px;width:6px;height:24px;border-radius:3px;background:#fff;border:2px solid #111;transform:translateX(-50%);box-shadow:0 2px 8px rgba(0,0,0,.35);}
.leaders{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px;}
.ld{border:1px solid var(--line);border-radius:12px;padding:7px 11px;background:var(--soft);font-size:.84rem;}
.ld b{font-weight:800;margin-right:6px;}
@media(max-width:700px){.fn{grid-template-columns:120px 1fr 56px;font-size:.78rem;}}
.disc{font-size:.76rem;opacity:.6;text-align:center;margin:30px 0 6px;}
[data-testid="stMetric"]{border:1px solid var(--line);border-radius:14px;padding:12px 14px;background:var(--soft);}
[data-baseweb="tab-list"]{gap:6px;flex-wrap:wrap;}
button[data-baseweb="tab"],button[data-testid="stTab"],.stTabs button[role="tab"]{height:auto !important;padding:12px 18px !important;border-radius:12px 12px 0 0;}
button[data-baseweb="tab"],button[data-baseweb="tab"] p,button[data-baseweb="tab"] div,button[data-baseweb="tab"] span,
button[data-testid="stTab"] p,.stTabs button[role="tab"] p{font-size:1.15rem !important;font-weight:700 !important;line-height:1.3 !important;}
button[data-baseweb="tab"][aria-selected="true"],button[data-testid="stTab"][aria-selected="true"]{background:var(--soft);}
.stButton>button{border-radius:12px;font-weight:600;}
@media(max-width:700px){button[data-baseweb="tab"],button[data-testid="stTab"]{padding:9px 11px !important;}button[data-baseweb="tab"],button[data-baseweb="tab"] p,button[data-baseweb="tab"] div,button[data-baseweb="tab"] span,button[data-testid="stTab"] p,.stTabs button[role="tab"] p{font-size:1rem !important;}.hb{grid-template-columns:96px 1fr 80px;}.sc .val{font-size:1.7rem;}.hero h1{font-size:1.45rem;}}
"""

def inject_css():
    st.markdown(f"<style>{_collapse(CSS)}</style>", unsafe_allow_html=True)

def inr(x, d=0) -> str:
    try:
        v = float(x)
        if not math.isfinite(v):
            return "₹–"
        txt = f"₹{abs(v):,.{d}f}"
        return ("-" + txt) if round(v, d) < 0 else txt
    except Exception:
        return "₹–"

def friendly_status(status, be_locked: bool = False) -> str:
    o = classify_outcome(status)
    if o == "ACTIVE":
        return "🟢 Open (profit protected)" if be_locked else "🟢 Open"
    return {"WIN": "✅ Won — hit the goal", "LOSS": "❌ Lost — safety exit hit",
            "BE": "🛡️ Saved — sold with no loss", "EXPIRED": "⏱️ Closed after 7 days"}.get(o, str(status))

def outcome_style(status) -> tuple:
    return {"WIN": ("Won", "good"), "LOSS": ("Lost", "bad"), "BE": ("Saved", "warn"),
            "EXPIRED": ("Time's up", "warn"), "ACTIVE": ("Open", "info")}.get(classify_outcome(status), (str(status)[:12], "info"))

def sec_title(icon: str, title: str, sub: str = ""):
    small = f"<small>{esc(sub)}</small>" if sub else ""
    st.markdown(_collapse(f'<div class="sec"><div class="bar"></div><h3>{esc(icon)} {esc(title)}{small}</h3></div>'), unsafe_allow_html=True)

def callout(kind: str, icon: str, text_html: str):
    st.markdown(_collapse(f'<div class="callout {kind}"><div class="ic">{icon}</div><div>{text_html}</div></div>'), unsafe_allow_html=True)

def score_cards(items: list):
    cards = [f'<div class="sc {kind}"><div class="lbl">{esc(label)}</div><div class="val {vcls}">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>'
             for label, value, sub, kind, vcls in items]
    st.markdown(_collapse('<div class="score">' + "".join(cards) + '</div>'), unsafe_allow_html=True)

def stat_card(label: str, value: str, sub: str = ""):
    st.markdown(_collapse(f'<div class="stat"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div><div class="sub2">{esc(sub)}</div></div>'), unsafe_allow_html=True)

def col_header(kind: str, title: str, n: int):
    st.markdown(_collapse(f'<div class="colh {kind}"><div>{esc(title)}</div><span class="n">{n}</span></div>'), unsafe_allow_html=True)

def empty_box(text: str):
    st.markdown(_collapse(f'<div class="empty">{esc(text)}</div>'), unsafe_allow_html=True)

def hbars_html(rows: list) -> str:
    if not rows:
        return ""
    m = max(abs(v) for _, v, _ in rows) or 1.0
    parts = []
    for label, v, txt in rows:
        w = max(4.0, abs(v) / m * 100.0)
        parts.append(f'<div class="hb"><div>{esc(label)}</div><div class="t"><div class="f {"pos" if v >= 0 else "neg"}" style="width:{w:.0f}%"></div></div><div><b>{esc(txt)}</b></div></div>')
    return _collapse('<div class="hbars">' + "".join(parts) + '</div>')

def funnel_html(rows: list) -> str:
    """rows: [(label, count)] from widest to narrowest."""
    rows = [(lbl, int(max(0, n))) for lbl, n in rows]
    top = max([n for _, n in rows] + [1])
    parts = []
    for i, (lbl, n) in enumerate(rows):
        w = 0.0 if n == 0 else max(1.5, n / top * 100.0)
        last = " last" if i == len(rows) - 1 else ""
        parts.append(f'<div class="fn"><div>{esc(lbl)}</div><div class="t"><div class="f{last}" style="width:{w:.1f}%"></div></div><div class="c">{n:,}</div></div>')
    return _collapse('<div class="funnel">' + "".join(parts) + '</div>')

def breadth_word(pct: float) -> str:
    pct = round(float(pct))
    if pct >= 60:
        return "Rising broadly"
    if pct >= 45:
        return "Mixed"
    if pct >= 30:
        return "Mostly weak"
    return "Falling broadly"

def gauge_html(pct: float) -> str:
    p = max(0.0, min(100.0, float(pct)))
    return _collapse(f'<div class="gauge"><div class="needle" style="left:{p:.1f}%"></div></div>'
                     '<div class="rlab" style="margin-bottom:14px"><span>Most shares falling</span><span>Half and half</span><span>Most shares rising</span></div>')

def sector_heat_html(breadth: dict, min_n: int = 5, top: int = 8) -> str:
    rows = [(sec, up / n * 100.0, up, n) for sec, (up, n) in breadth.items() if n >= min_n and sec != "Other"]
    rows.sort(key=lambda x: (-x[1], -x[3]))
    if not rows:
        return ""
    parts = []
    for sec, pct, up, n in rows[:top]:
        cls = "pos" if pct >= 50 else "neg"
        parts.append(f'<div class="hb"><div>{esc(sec)}</div><div class="t"><div class="f {cls}" style="width:{max(3.0, pct):.0f}%"></div></div>'
                     f'<div><b>{pct:.0f}% rising</b> <span class="sub2">of {n}</span></div></div>')
    return _collapse('<div class="hbars">' + "".join(parts) + '</div>')

def leaders_html(leaders: list) -> str:
    if not leaders:
        return ""
    chips = [f'<span class="ld"><b>{esc(t)}</b><span class="{"pos" if rs >= 0 else "neg"}">{rs:+.1f}%</span> <span class="sub2">{esc(sec)}</span></span>'
             for t, sec, rs in leaders]
    return _collapse('<div class="leaders">' + "".join(chips) + '</div>')

def price_bar_html(entry: float, target: float, stop: float, live: float, compact: bool = False) -> str:
    span = target - stop
    if span <= 0:
        return ""
    pos = lambda v: max(0.0, min(100.0, (v - stop) / span * 100.0))
    bar = f'<div class="rbar{" c" if compact else ""}"><div class="rtick" style="left:{pos(entry):.1f}%"></div><div class="rdot" style="left:{pos(live):.1f}%"></div></div>'
    if compact:
        return _collapse(bar)
    return _collapse(bar + f'<div class="rlab"><span>🛑 Exit if it falls to ₹{stop:,.2f}</span><span>Bought ₹{entry:,.2f}</span><span>🎯 Goal ₹{target:,.2f}</span></div>')

def _fmt_rs(v) -> str:
    try:
        v = float(v)
        return f"{v:+.1f}%" if math.isfinite(v) else "–"
    except Exception:
        return "–"

def signal_card_html(row) -> str:
    conf = int(round(float(row["Adjusted Score"])))
    colour = "#16a34a" if conf >= 75 else ("#f97316" if conf >= 65 else "#d97706")
    ready = '<span class="tag good">Ready to buy</span>' if bool(row.get("Loggable", False)) else '<span class="tag warn">Watch only</span>'
    return _collapse(f"""
    <div class="tcard">
      <div class="tags"><span class="tag">{esc(row['Sector'])}</span>{ready}</div>
      <div class="row"><div><div class="tk">{esc(row['Ticker'])}</div><div class="sub2">AI confidence</div></div>
      <div class="ringwrap"><div class="ring" style="background:conic-gradient({colour} {conf}%,rgba(128,128,128,.25) 0)"></div><div class="ringnum">{conf}%</div></div></div>
      <div class="big pos" style="margin-top:8px">{esc(str(row['Expected Return']))}</div><div class="sub2">possible profit if it reaches the goal</div>
      {price_bar_html(float(row['Price (₹)']), float(row['Target (₹)']), float(row['Stop Loss (₹)']), float(row['Price (₹)']))}
      <div class="grid2"><div><b>Shares</b>{esc(row['Recommended Shares'])}</div><div><b>Money in</b>{inr(row['RawCapital'])}</div>
      <div><b>Costs</b>{float(row['Costs %']):.2f}%</div><div><b>Easy to trade</b>₹{row.get('Easy to trade (₹cr/day)', 0)}cr/day</div>
      <div><b>Vs Nifty</b>{_fmt_rs(row.get('Vs Nifty (20d)'))} (20 days)</div><div><b>Sell by</b>{MAX_HOLD_CALENDAR_DAYS} days at most</div></div>
      <div class="note">🧠 {esc(row['Why'])}</div>
      <div class="note">📰 {esc(row['FilingStatus'])}</div>
    </div>
    """)

def mini_trade_html(ticker, sector, entry, target, stop, live, pnl_pct, locked, shares) -> str:
    cls = "pos" if pnl_pct >= 0 else "neg"
    return _collapse(f"""
    <div class="mini">
      <div class="row"><span class="mtk">{esc(ticker)}{' 🛡️' if locked else ''}</span><span class="mp {cls}">{pnl_pct:+.2f}%</span></div>
      {price_bar_html(entry, target, stop, live, compact=True)}
      <div class="sub2">{esc(sector)} · now ₹{live:,.2f} · {int(shares)} shares</div>
    </div>
    """)

def option_short_name(contract) -> str:
    return str(contract).split(" (")[0]

def option_mini_html(o) -> str:
    entry, target, stop = float(o["entry_premium"]), float(o["target_premium"]), float(o["stop_loss_premium"])
    live, pnl = float(o["current_option_price"]), float(o["pnl_pct"])
    src = '<span class="tag good">Live NSE quote</span>' if is_live_option(o["option_contract"]) else '<span class="tag warn">Estimated price</span>'
    cls = "pos" if pnl >= 0 else "neg"
    return _collapse(f"""
    <div class="mini opt">
      <div class="tags"><span class="tag info">Option</span>{src}</div>
      <div class="row"><span class="mtk">{esc(option_short_name(o['option_contract']))}</span><span class="mp {cls}">{pnl:+.2f}%</span></div>
      {price_bar_html(entry, target, stop, live, compact=True)}
      <div class="sub2">bought ₹{entry:,.2f} · now ₹{live:,.2f} · 1 lot of {int(float(o['lot_size']))} · since {esc(str(o['date_key']))}</div>
    </div>
    """)

def finished_row_html(ticker, status, pnl_pct, when) -> str:
    s_text, s_kind = outcome_style(status)
    cls = "pos" if pnl_pct >= 0 else "neg"
    return _collapse(f'<div class="fin"><div><b>{esc(ticker)}</b> <span class="tag {s_kind}">{esc(s_text)}</span><div class="sub2">{esc(when)}</div></div><div class="mp {cls}">{pnl_pct:+.2f}%</div></div>')

# ---------- charts ----------
def show_fig(fig, key=None):
    if not PLOTLY_OK or fig is None:
        return
    try:
        st.plotly_chart(fig, width="stretch", key=key)
    except TypeError:
        st.plotly_chart(fig, use_container_width=True, key=key)

def _layout(fig, height=320, title=None):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=44 if title else 16, b=10), title=title,
                      legend=dict(orientation="h", y=1.14), hovermode="x unified")
    return fig

def chart_cumulative(perf_df: pd.DataFrame, title: str, key: str = None):
    if perf_df.empty:
        return
    x = [str(d) for d in perf_df["exit_date"]]
    cum = perf_df["pnl_inr"].cumsum()
    if not PLOTLY_OK:
        st.line_chart(pd.DataFrame({"Running profit/loss (₹)": cum.values}, index=x))
        return
    colour = "#16a34a" if cum.iloc[-1] >= 0 else "#dc2626"
    fig = go.Figure(go.Scatter(x=x, y=cum, mode="lines+markers", line=dict(color=colour, width=3, shape="hv"), fill="tozeroy",
                               name="Running profit/loss (₹)", hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>"))
    show_fig(_layout(fig, 320, title), key=key)

def chart_outcomes(perf: dict, title: str = "How your trades ended", key: str = None):
    labels, vals = ["Hit the goal", "Safety exit", "Sold with no loss", "Time ran out"], [perf["wins"], perf["losses"], perf["be"], perf["expired"]]
    if not PLOTLY_OK:
        st.bar_chart(pd.DataFrame({title: vals}, index=labels))
        return
    fig = go.Figure(go.Pie(labels=labels, values=vals, hole=0.62, sort=False, marker=dict(colors=["#16a34a", "#dc2626", "#d97706", "#64748b"]), textinfo="label+percent"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=44, b=10), title=title, showlegend=False)
    show_fig(fig, key=key)

def chart_hist(perf_df: pd.DataFrame):
    if perf_df.empty:
        return
    if not PLOTLY_OK:
        st.bar_chart(perf_df["pnl_pct"].round(1).value_counts().sort_index())
        return
    fig = go.Figure(go.Histogram(x=perf_df["pnl_pct"], nbinsx=24, marker=dict(color="#f97316"), hovertemplate="%{x:.1f}% : %{y} trades<extra></extra>"))
    fig.add_vline(x=0, line_dash="dash", line_color="gray")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=44, b=10), title="How much each trade made or lost (%) — right of the dashed line = profit", bargap=0.05)
    show_fig(fig)

def chart_sector(exp: dict):
    if not exp:
        return
    items = sorted(exp.items(), key=lambda x: x[1])
    if not PLOTLY_OK:
        st.bar_chart(pd.DataFrame({"open trades": [v for _, v in items]}, index=[k for k, _ in items]))
        return
    fig = go.Figure(go.Bar(x=[v for _, v in items], y=[k for k, _ in items], orientation="h",
                           marker=dict(color=["#dc2626" if v >= sector_cap(k) else "#f97316" for k, v in items])))
    fig.update_layout(height=max(220, 38 * len(items) + 60), margin=dict(l=10, r=10, t=40, b=10), title="Open trades per sector (red = full)", xaxis=dict(dtick=1))
    show_fig(fig)

def calibration_rows(closed_df: pd.DataFrame) -> list:
    rows = []
    if closed_df is None or closed_df.empty:
        return rows
    for _, r in closed_df.iterrows():
        o = classify_outcome(r["status"])
        if o not in ("WIN", "LOSS"):
            continue
        f = _parse_features(r["features_json"])
        try:
            if f.get("final_score") is not None:
                conf = float(f["final_score"])
            elif f.get("base_ml_conf") is not None:
                conf = float(f["base_ml_conf"]) + float(f.get("learner_delta", 0)) + float(f.get("macro_delta", 0)) + float(f.get("nlp_delta", 0))
            else:
                continue
        except Exception:
            continue
        rows.append((conf, 1 if o == "WIN" else 0))
    return rows

def chart_calibration(rows: list):
    d = pd.DataFrame(rows, columns=["conf", "hit"])
    d["bucket"] = pd.cut(d["conf"], bins=[0, 60, 70, 80, 101], labels=["Under 60%", "60–70%", "70–80%", "80%+"])
    g = d.groupby("bucket", observed=True).agg(stated=("conf", "mean"), realised=("hit", lambda s: s.mean() * 100.0), n=("hit", "size")).reset_index()
    if not PLOTLY_OK:
        st.dataframe(g.rename(columns={"bucket": "AI confidence", "stated": "AI said (%)", "realised": "Actually won (%)", "n": "Trades"}), hide_index=True)
        return
    fig = go.Figure()
    fig.add_bar(x=g["bucket"].astype(str), y=g["stated"], name="What the AI said", marker_color="#f97316")
    fig.add_bar(x=g["bucket"].astype(str), y=g["realised"], name="How often it actually won", marker_color="#16a34a", customdata=g["n"],
                hovertemplate="%{y:.0f}% won (from %{customdata} trades)<extra></extra>")
    fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=44, b=10), title="When the AI says “X% sure”, how often is it right?",
                      yaxis=dict(range=[0, 100], title="%"), legend=dict(orientation="h", y=1.14))
    show_fig(fig)

def chart_rolling(perf_df: pd.DataFrame, window: int = 10):
    n = len(perf_df)
    if n < window + 5:
        st.info(f"This chart appears once {window + 5} trades have finished (so far: {n}).")
        return
    wins = (perf_df["outcome"] == "WIN").astype(float)
    roll = (wins.rolling(window).mean() * 100.0).tolist()
    overall = float(wins.mean() * 100.0)
    x = list(range(1, n + 1))
    if not PLOTLY_OK:
        st.line_chart(pd.DataFrame({f"Win rate of last {window} trades (%)": roll}, index=x))
        return
    fig = go.Figure(go.Scatter(x=x, y=roll, mode="lines", line=dict(color="#f97316", width=3), hovertemplate="After trade %{x}: %{y:.0f}% won<extra></extra>"))
    fig.add_hline(y=overall, line_dash="dash", line_color="gray", annotation_text=f"overall {overall:.0f}%", annotation_position="top left")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10), yaxis=dict(range=[0, 100], title="% won"), xaxis_title="Finished trade number", showlegend=False)
    show_fig(fig)

def style_ledger(df: pd.DataFrame):
    try:
        def pnl_color(v):
            try:
                return "color:#16a34a;font-weight:700" if float(v) > 0 else ("color:#dc2626;font-weight:700" if float(v) < 0 else "")
            except Exception:
                return ""
        sty = df.style.format({"Bought at (₹)": "{:,.2f}", "Goal (₹)": "{:,.2f}", "Safety exit (₹)": "{:,.2f}", "Price now (₹)": "{:,.2f}", "Profit/Loss (%)": "{:+.2f}%"}, na_rep="-")
        mapper = sty.map if hasattr(sty, "map") else sty.applymap
        return mapper(pnl_color, subset=["Profit/Loss (%)"])
    except Exception:
        return df

# ---------- learning helpers ----------
def compute_memory(closed_df: pd.DataFrame) -> tuple:
    careful, liked = [], []
    if closed_df is None or closed_df.empty:
        return careful, liked
    today_dt = ist_now().date()
    acc = {}
    for _, r in closed_df.iterrows():
        o = classify_outcome(r["status"])
        if o not in ("WIN", "LOSS"):
            continue
        d = _to_date(_first_valid(r.get("exit_timestamp"), r.get("last_audited"), r.get("date_str")))
        w = math.pow(0.5, (max(0, (today_dt - d).days) if d else 15) / HALF_LIFE_DAYS)
        a = acc.setdefault(clean_sym_name(r["ticker"]), {"loss": 0.0, "win": 0.0})
        a["loss" if o == "LOSS" else "win"] += (15.0 if o == "LOSS" else 5.0) * w
    for t, a in acc.items():
        if a["loss"] >= 1.0:
            careful.append((t, int(round(a["loss"])), int(round(HALF_LIFE_DAYS * math.log2(max(a["loss"], 1.0))))))
        if a["win"] >= 1.0:
            liked.append((t, int(round(a["win"])), int(round(HALF_LIFE_DAYS * math.log2(max(a["win"], 1.0))))))
    careful.sort(key=lambda x: -x[1])
    liked.sort(key=lambda x: -x[1])
    return careful, liked

def sector_stats(perf_df: pd.DataFrame) -> pd.DataFrame:
    if perf_df.empty:
        return pd.DataFrame()
    d = perf_df.copy()
    d["Sector"] = d.apply(lambda x: get_trade_sector(x["ticker"], x["features_json"]), axis=1)
    g = d.groupby("Sector").agg(n=("pnl_pct", "size"), avg=("pnl_pct", "mean"), wins=("outcome", lambda s: (s == "WIN").sum())).reset_index()
    g["win_rate"] = g["wins"] / g["n"] * 100.0
    return g

def build_insights(perf_df: pd.DataFrame) -> list:
    out = []
    if perf_df.empty:
        return out
    sg = sector_stats(perf_df)
    sg3 = sg[sg["n"] >= 3] if not sg.empty else sg
    if not sg3.empty:
        best, worst = sg3.sort_values("avg", ascending=False).iloc[0], sg3.sort_values("avg").iloc[0]
        out.append(f"Best sector so far: **{best['Sector']}** (average {best['avg']:+.1f}% per trade over {int(best['n'])} trades).")
        if worst["Sector"] != best["Sector"]:
            out.append(f"Toughest sector so far: **{worst['Sector']}** (average {worst['avg']:+.1f}% per trade over {int(worst['n'])} trades).")
    try:
        frs = [get_trade_friction_pct(r["features_json"]) for _, r in perf_df.iterrows()]
        out.append(f"Government taxes, broker and exchange charges take about **{np.mean(frs):.2f}%** off every trade — a trade has to beat that before it earns anything.")
    except Exception:
        pass
    wins, losses = perf_df[perf_df["outcome"] == "WIN"], perf_df[perf_df["outcome"] == "LOSS"]
    try:
        def hold(df_):
            v = [(b - a).days for a, b in zip(df_["entry_date"], df_["exit_date"]) if a is not None and b is not None]
            return float(np.mean(v)) if v else None
        hw, hl = hold(wins), hold(losses)
        if hw is not None and hl is not None and len(wins) >= 3 and len(losses) >= 3:
            out.append(f"Winners reached their goal in about **{hw:.1f} days**; losing trades hit their safety exit after about **{hl:.1f} days**.")
    except Exception:
        pass
    try:
        def atr_mean(df_):
            v = [float(_parse_features(r["features_json"]).get("atr_pct")) for _, r in df_.iterrows() if _parse_features(r["features_json"]).get("atr_pct") is not None]
            return (float(np.mean(v)), len(v)) if v else (None, 0)
        (aw, nw), (al, nl) = atr_mean(wins), atr_mean(losses)
        if aw is not None and al is not None and nw >= 5 and nl >= 5:
            if al > aw + 0.3:
                out.append(f"Losing trades were on shares that move about **{al:.1f}% a day**, versus **{aw:.1f}%** for winners — very jumpy shares have been riskier, so the AI is wary of them.")
            else:
                out.append(f"Winners and losers moved a similar amount day to day ({aw:.1f}% vs {al:.1f}%), so jumpiness hasn't been the deciding factor.")
    except Exception:
        pass
    return out

def current_streak(perf_df: pd.DataFrame) -> tuple:
    if perf_df.empty:
        return 0, True
    signs = (perf_df["pnl_inr"] > 0).tolist()
    k = 0
    for s in reversed(signs):
        if s == signs[-1]:
            k += 1
        else:
            break
    return k, signs[-1]

# ==============================================================================
# 14. SIDEBAR & AUTONOMOUS LOOP
# ==============================================================================
inject_css()
state = get_market_state()
market_is_open = is_nse_market_open()

st.sidebar.header("⚙️ Settings")
st.sidebar.caption("🔭 Looks at **every share on the NSE** each time — no baskets, no price limit.")
st.sidebar.number_input("Broker fee per order (₹)", min_value=0.0, max_value=100.0, value=DEFAULT_BROKERAGE_PER_ORDER_INR, step=1.0, key="brokerage_inr",
                        help="Many brokers charge ₹0 for delivery trades. Charged on buy and again on sell.")
st.sidebar.number_input("Depository charge per sale (₹)", min_value=0.0, max_value=50.0, value=DEFAULT_DP_CHARGE_INR, step=0.5, key="dp_charge_inr",
                        help="A small fixed fee each time you sell shares. Check your broker's rate.")
if "auto_loop_toggle" not in st.session_state or st.session_state.get("last_market_open_state") != market_is_open:
    st.session_state["auto_loop_toggle"] = market_is_open
    st.session_state["last_market_open_state"] = market_is_open
auto_mode = st.sidebar.toggle("Auto-refresh", key="auto_loop_toggle", help="Turns on by itself when the NSE opens and off when it closes (including holidays).")
refresh_options = {"5 minutes": 300, "15 minutes": 900, "1 hour": 3600}
selected_interval = st.sidebar.selectbox("Check every", list(refresh_options.keys()), index=0,
                                         help="Open trades are checked every time. Prices for the whole market are re-downloaded at most every 15 minutes; finalists always get a fresh price.")
refresh_interval_sec = refresh_options[selected_interval]

with st.sidebar.expander("🛡️ Built-in safety rules"):
    st.markdown(
        f"- Max **{MAX_DAILY_EQUITY_TRADES} new trades** a day, **{MAX_OPEN_POSITIONS} open** at once\n"
        f"- Max **{MAX_ACTIVE_PER_SECTOR} trades** in any one sector\n"
        f"- About **₹{POSITION_SIZE_INR:,.0f}** per trade; total money at risk capped near ₹{MAX_OPEN_RISK_INR:,.0f}\n"
        f"- At about **{int(BREAKEVEN_TRIGGER_RATIO * 100)}%** of the way to the goal, the safety exit moves up so the trade can't lose money\n"
        f"- No re-buying a stock for **{COOLDOWN_CALENDAR_DAYS} days** after selling it\n"
        f"- Buying only **{ENTRY_WINDOW_START.strftime('%H:%M')}–{ENTRY_WINDOW_END.strftime('%H:%M')} IST** on real trading days (weekends and NSE holidays are skipped)\n"
        f"- Skips thinly traded shares (under ₹{MIN_AVG_TURNOVER_INR / 1e7:.0f} crore/day), shares under ₹{MIN_PRICE_INR:.0f}, shares stuck at their price limit, and shares that swing more than {MAX_ATR_PCT:.0f}% on a normal day\n"
        f"- Buying pauses if the Nifty falls sharply or India VIX is very high\n"
        f"- Every result includes taxes, charges and slippage")

with st.sidebar.expander("🏖️ Market holidays"):
    ups = upcoming_holidays(5)
    if ups:
        for d_, name_ in ups:
            st.write(f"**{datetime.strptime(d_, '%Y-%m-%d').strftime('%a %d %b %Y')}** — {name_}")
    else:
        st.caption("NSE's holiday list isn't reachable right now. The app still skips holidays by checking whether the Nifty has a price for today.")

loop_tick = 0
if auto_mode and st_autorefresh:
    loop_tick = st_autorefresh(interval=refresh_interval_sec * 1000, key="nse_unified_autorefresh")

is_new_loop_tick = ("last_loop_tick" not in st.session_state) or (loop_tick != st.session_state["last_loop_tick"])
if is_new_loop_tick:
    audit_and_reconcile_all_trades()
    st.session_state["last_loop_tick"] = loop_tick
    st.session_state["force_refresh"] = True

with st.sidebar.expander("🩺 System health"):
    try:
        prices_ok = not fetch_cached_history("^NSEI", period="5d", interval="1d").empty
    except Exception:
        prices_ok = False
    st.write(f"{'🟢' if prices_ok else '🔴'} Share prices {'arriving' if prices_ok else 'not arriving'}")
    st.write(f"{'🟢' if supabase else '🔴'} Online backup {'connected' if supabase else 'not connected'}")
    st.write(f"{'🟢' if get_ml_model() is not None else '🔴'} AI model {'loaded' if get_ml_model() is not None else 'missing'}")
    st.write(f"{'🟢' if ANNOUNCEMENTS_AVAILABLE else '⚪'} News check {'available' if ANNOUNCEMENTS_AVAILABLE else 'module not found'}")
    st.write(f"🕒 Last price check: {st.session_state.get('last_price_check', '-')}")
    _ub = get_nse_http_box().get("universe")
    if _ub:
        st.write(f"{'🟢' if _ub[3] else '🟠'} Share list: {len(_ub[1]):,} shares — {_ub[2]}")
    _ib = get_nse_http_box().get("industries")
    if _ib:
        st.write(f"{'🟢' if _ib[1] else '🟠'} Sector labels: {len(_ib[1]):,} shares labelled by NSE" + ("" if _ib[1] else " (not reachable — unknown shares go in 'Other')"))
    jc, hi_ = journal_counts(), st.session_state.get("hydrate_info", {})
    st.write(f"📒 Journal: {jc['total']} share trades — {jc['open']} open, {jc['closed']} finished")
    try:
        _oj = load_options_journal_df()
        _oo = int((_oj["status"].apply(classify_outcome) == "ACTIVE").sum()) if not _oj.empty else 0
        st.write(f"📜 Options: {len(_oj)} ideas — {_oo} open, {len(_oj) - _oo} finished")
    except Exception:
        pass
    if supabase:
        st.write(f"☁️ Backup: {hi_.get('remote', 0)} trades online ({hi_.get('remote_active', 0)} open) · restored {hi_.get('restored', 0)} · skipped {hi_.get('bad_rows', 0)}")
    if not st_autorefresh:
        st.caption("Auto-refresh library not installed — use “Find new picks / refresh” to update.")

if "retrain_notice" in st.session_state:
    st.sidebar.success(st.session_state["retrain_notice"])
if "ml_model_source" in st.session_state:
    st.sidebar.caption(f"🤖 AI version in use: **{st.session_state['ml_model_source']}**")
if "db_error" in st.session_state:
    st.sidebar.error(f"⚠️ Online backup problem: {st.session_state['db_error']}")
if st.session_state.get("pending_cloud_sync"):
    st.sidebar.warning(f"⏳ {len(st.session_state['pending_cloud_sync'])} update(s) waiting to be saved online.")

if st.sidebar.button("🚪 Log Out", width="stretch"):
    st.query_params.clear()
    st.session_state["password_correct"] = False
    st.rerun()

# ==============================================================================
# 15. PAGE LAYOUT: HERO (filled later), TABS, THEN THE SCAN INSIDE THE BOARD TAB
# ==============================================================================
hero_box = st.container()
tab_board, tab_results, tab_learn, tab_options = st.tabs(["📋 My Board", "📈 Results", "🧠 What the AI Learned", "🎲 Options"])

if "dispatched_orders" not in st.session_state:
    st.session_state["dispatched_orders"] = set()

with tab_board:
    h1, h2 = st.columns([4, 1.3])
    with h1:
        sec_title("📋", "Your board", "ready to buy → running → finished")
    with h2:
        re_scan = st.button("🔄 Find new picks / refresh", width="stretch", type="primary")

    todays_df = get_todays_logged_equities()
    quota_filled = len(todays_df) >= MAX_DAILY_EQUITY_TRADES
    pulse_stale = (time.time() - float(st.session_state.get("scan_epoch", 0.0))) > PULSE_REFRESH_SEC
    need_scan = re_scan or "scan_results" not in st.session_state or (
        st.session_state.get("force_refresh", False) and (not quota_filled or pulse_stale))
    if need_scan:
        st.session_state["force_refresh"] = False
        with st.spinner("Checking every NSE share and your open trades (the first check of the day takes a minute or two)..."):
            if re_scan:
                hydrate_duckdb_from_supabase()
                audit_and_reconcile_all_trades()
            res_df, has_cleared = run_predictions()
            st.session_state["scan_results"] = res_df
            st.session_state["has_cleared"] = has_cleared
            st.session_state["scan_ts"] = ist_now().strftime("%H:%M:%S")
            st.session_state["scan_epoch"] = time.time()
    st.session_state["force_refresh"] = False

if is_new_loop_tick:
    st.session_state["last_audit_str"] = ist_now().strftime("%d %b %H:%M")

# ==============================================================================
# 16. OPTIONS IDEA + DATA LOAD (after the scan, so anything just bought is included)
# ==============================================================================
opt_signal = generate_daily_options_alpha()

df_eq, df_opt = pd.DataFrame(), pd.DataFrame()
with DB_LOCK:
    _con = duckdb.connect(DB_PATH, read_only=True)
    try:
        df_eq = _con.execute("SELECT * FROM trade_journal WHERE ticker NOT LIKE '%.L' ORDER BY date_str DESC, ticker ASC").df()
        df_opt = _con.execute("SELECT * FROM daily_options_journal ORDER BY date_key DESC").df()
    except Exception:
        pass
    finally:
        _con.close()

perf_eq_df = prep_perf_frame(df_eq, "capital_allocated")
perf_opt_df = prep_perf_frame(df_opt, "total_capital", is_option=True)
perf = compute_performance(perf_eq_df)
perf_o = compute_performance(perf_opt_df)
active_opt = df_opt[df_opt["status"].apply(classify_outcome) == "ACTIVE"].copy() if not df_opt.empty else pd.DataFrame()
opt_open_unreal = float((pd.to_numeric(active_opt["total_capital"], errors="coerce").fillna(0) * pd.to_numeric(active_opt["pnl_pct"], errors="coerce").fillna(0) / 100.0).sum()) if not active_opt.empty else 0.0
opt_learn = options_learning(df_opt)
active_eq = df_eq[df_eq["status"].apply(classify_outcome) == "ACTIVE"].copy() if not df_eq.empty else pd.DataFrame()
active_cap = float(active_eq["capital_allocated"].sum()) if not active_eq.empty else 0.0
open_unreal = float((active_eq["capital_allocated"] * active_eq["pnl_pct"] / 100.0).sum()) if not active_eq.empty else 0.0
be_prot = int(sum(is_stop_breakeven_protected(float(r["entry_price"]), float(r["stop_loss"])) for _, r in active_eq.iterrows())) if not active_eq.empty else 0
open_unreal_all = open_unreal + opt_open_unreal
n_open_all = len(active_eq) + len(active_opt)

def open_split_text() -> str:
    """'3 shares · 1 option' — the same wording wherever open trades are counted."""
    parts = [f"{len(active_eq)} share{'s' if len(active_eq) != 1 else ''}"]
    if len(active_opt):
        parts.append(f"{len(active_opt)} option{'s' if len(active_opt) != 1 else ''}")
    return " · ".join(parts)

closed_all = load_closed_trades_df()
todays_df = get_todays_logged_equities()
cap_state = get_portfolio_capacity()
macro = get_indian_cross_asset_macro()
stress = is_market_stress(macro)

# ---------- hero ----------
def macro_chip(label, v, suffix="%"):
    cls = "up" if v >= 0 else "down"
    return f'<span class="chip {cls}">{esc(label)} {"▲" if v >= 0 else "▼"} {v:+.1f}{suffix}</span>'

if not state["trading_day"]:
    mkt_chip = f'<span class="chip warn">🏖️ {esc(state["reason"])}</span>'
elif market_is_open:
    mkt_chip = '<span class="chip up">NSE OPEN</span>'
else:
    mkt_chip = f'<span class="chip down">NSE CLOSED · opens {esc(next_open_text())}</span>'
if not state["trading_day"]:
    mkt_chip += f'<span class="chip">Next open: {esc(next_open_text())}</span>'
feed_chip = '<span class="chip down">⚠️ Price feed problem</span>' if st.session_state.get("price_feed_problem") else ""
stress_chip = '<span class="chip down">⛔ Buying paused — market under stress</span>' if stress else ""
_ps = st.session_state.get("scan_stats", {}) or {}
if _ps.get("scanned"):
    _bp = _ps.get("uptrend", 0) / max(1, _ps["scanned"]) * 100.0
    pulse_chip = f'<span class="chip {"up" if _bp >= 50 else "down"}">🔭 {_ps["scanned"]:,} shares checked · {_bp:.0f}% rising</span>'
else:
    pulse_chip = ""
with hero_box:
    st.markdown(_collapse(f"""
    <div class="hero">
      <h1>⚡ Autonomous AI Quant Terminal — NSE</h1>
      <div class="sub">An AI that picks Indian shares and practises trading them with pretend money — so you can see honestly whether it works.</div>
      <div class="chips">{mkt_chip}
        <span class="chip {'up' if supabase else 'down'}">{'☁️ Saved online' if supabase else '☁️ Not saved online'}</span>
        {macro_chip('Nifty (5 days)', macro['nifty_5d'])}{macro_chip('USD/INR', macro['usdinr_5d'])}{macro_chip('Brent oil', macro['brent_5d'])}
        <span class="chip {'down' if macro['vix_level'] >= STRESS_VIX else ''}">India VIX {macro['vix_level']}</span>
        {stress_chip}{feed_chip}
        {pulse_chip}
        <span class="chip">Last price check {esc(st.session_state.get('last_price_check', '-'))}</span>
      </div>
    </div>"""), unsafe_allow_html=True)
    if n_open_all:
        story = f"<b>{n_open_all} trade{'s' if n_open_all != 1 else ''} running</b> ({open_split_text()}, {inr(open_unreal_all)} so far)."
    else:
        story = "<b>Nothing running right now.</b>"
    if perf["n"]:
        story += f" <b>{perf['n']} share trade{'s' if perf['n'] != 1 else ''} finished</b>, {perf['wins']} hit their goal, {inr(perf['net_inr'])} after all costs."
    else:
        story += " No share trade has finished yet, so there are no results to judge."
    if perf_o["n"]:
        story += f" Options: {perf_o['n']} finished, {inr(perf_o['net_inr'])} (kept separate)."
    callout("good" if open_unreal_all > 0 else ("bad" if open_unreal_all < 0 else "info"), "📍", story)
    with st.expander("💡 New here? 30-second guide"):
        st.markdown(
            "- This app **pretends to buy** Indian shares, about **₹25,000 each**. It's practice — no real money is used and nothing is sent to a broker.\n"
            "- Every time it checks, it looks at **every share listed on the NSE**, throws out the ones that are hard to trade or risky, and only buys the very best.\n"
            "- Every pick has a **Goal** (sell for a profit) and a **Safety exit** (sell to limit the loss).\n"
            "- When a trade gets about two-thirds of the way to its goal, the safety exit moves up so it **can't lose money** any more.\n"
            "- **AI confidence** is how sure the AI is that the goal is hit before the safety exit. It's a guide, not a promise.\n"
            "- Every profit/loss number is **after STT, stamp duty, exchange charges, depository fee, broker fee and slippage**.\n"
            "- The NSE is closed on weekends and public holidays — the app knows and does nothing on those days.\n"
            "- A trade closes after **7 days** if neither the goal nor the safety exit is reached.")

# ==============================================================================
# 17. TAB: MY BOARD
# ==============================================================================
with tab_board:
    if "flash" in st.session_state:
        st.info(st.session_state.pop("flash"))
    if not state["trading_day"]:
        callout("warn", "🏖️", f"<b>The NSE is closed today ({esc(state['reason'])}).</b> Nothing will be bought, and open trades keep the last trading day's prices. Next open: {esc(next_open_text())}.")
    elif stress:
        callout("bad", "⛔", "<b>Buying is paused.</b> The Nifty has fallen sharply this week or India VIX is very high. Picks are shown for information only.")
    elif not cap_state["ok"]:
        callout("warn", "🧯", f"<b>{esc(cap_state['reason'])}.</b> New buying resumes when some trades close.")
    elif not is_entry_window_open():
        callout("info", "🕗", "The market isn't in its buying window (09:30–15:00 IST, trading days). Picks are a watch-list, so every purchase is made at a real price.")

    hi_ = st.session_state.get("hydrate_info", {})
    if supabase and (hi_.get("remote_active", 0) > len(active_eq) or hi_.get("bad_rows", 0)):
        callout("warn", "☁️", f"<b>The online backup lists {hi_.get('remote_active', 0)} open share trade(s), but {len(active_eq)} show here.</b> "
                              f"{hi_.get('bad_rows', 0)} backup row(s) were unreadable ({esc(hi_.get('first_error', '') or 'none')}). Press “Find new picks / refresh” to retry. "
                              "(Trades that already closed here legitimately differ from an older backup.)")
    stats = st.session_state.get("scan_stats", {})
    if stats.get("error"):
        callout("bad", "🛑", esc(stats["error"]))
    elif stats:
        st.caption(f"Last check {st.session_state.get('scan_ts', '-')} (took {stats.get('seconds', 0):.0f}s): {stats.get('scanned', 0):,} tradable shares checked out of "
                   f"{stats.get('listed', 0):,} on the NSE · {stats.get('deep', 0)} finalists · bought {stats.get('logged', 0)} automatically · "
                   f"{len(todays_df)} of {MAX_DAILY_EQUITY_TRADES} picks used today")
        if stats.get("listed", 0) >= 100 and stats.get("priced", 0) < stats["listed"] * 0.5:
            callout("warn", "📡", f"<b>Prices arrived for only {stats.get('priced', 0):,} of {stats['listed']:,} shares.</b> The price service may be busy. "
                                  "Nothing is guessed: shares without prices are simply skipped, and the next check tries again.")
        elif stats.get("partial"):
            callout("info", "⏳", "<b>Some shares' prices were still downloading</b> when the time limit was reached. They'll be included in the next check.")
        if stats.get("source") and "today" not in str(stats.get("source")):
            callout("warn", "📃", f"<b>Share list:</b> {esc(stats['source'])}.")

    c_buy, c_run, c_done = st.columns([1.15, 1.05, 0.95])

    with c_buy:
        df_res = st.session_state.get("scan_results", pd.DataFrame())
        held_now = set(clean_sym_name(t) for t in (active_eq["ticker"].tolist() if not active_eq.empty else []))
        picks = df_res[(df_res["Qualified"] == True) & (~df_res["Ticker"].isin(held_now))].head(5) if not df_res.empty else pd.DataFrame()
        col_header("buy", "🔍 Ready to buy", len(picks))
        if len(todays_df) >= MAX_DAILY_EQUITY_TRADES:
            empty_box(f"All {MAX_DAILY_EQUITY_TRADES} picks for today are used. This stops over-trading.")
        elif picks.empty:
            empty_box("Nothing passes every check right now. Doing nothing is a valid choice.")
        else:
            today_key = ist_now().strftime('%Y-%m-%d')
            for _, row in picks.iterrows():
                st.markdown(signal_card_html(row), unsafe_allow_html=True)
                token = hashlib.sha256(f"{row['Ticker']}_{today_key}".encode()).hexdigest()[:12]
                if st.button(f"🚀 Buy {row['Ticker']} (paper trade)", key=f"buy_{token}", width="stretch", disabled=token in st.session_state["dispatched_orders"]):
                    ok = log_equity_signal_safely(row.to_dict(), enforce_sector_cap=True)
                    if ok:
                        st.session_state["dispatched_orders"].add(token)
                        st.session_state["flash"] = f"✅ Paper-bought {row['Ticker']}. Nothing was sent to a broker."
                    else:
                        st.session_state["flash"] = f"⚠️ Couldn't buy {row['Ticker']}: {st.session_state.get('last_log_reason', 'a safety rule blocked it')}."
                    st.rerun()

    with c_run:
        col_header("run", "🟢 Running", len(active_eq) + len(active_opt))
        if active_eq.empty and active_opt.empty:
            empty_box("No open trades.")
        else:
            if len(active_opt):
                st.caption(f"{open_split_text()} — option results are kept separate (🎲 Options tab)")
            for _, r in (active_eq.sort_values("pnl_pct", ascending=False).head(12).iterrows() if not active_eq.empty else []):
                st.markdown(mini_trade_html(r["ticker"], get_trade_sector(r["ticker"], r["features_json"]), float(r["entry_price"]), float(r["target_price"]),
                                            float(r["stop_loss"]), float(r["latest_price"]), float(r["pnl_pct"]),
                                            is_stop_breakeven_protected(float(r["entry_price"]), float(r["stop_loss"])), r["shares"]), unsafe_allow_html=True)
            if len(active_eq) > 12:
                st.caption(f"Showing 12 of {len(active_eq)} share trades.")
            for _, o in (active_opt.iterrows() if not active_opt.empty else []):
                st.markdown(option_mini_html(o), unsafe_allow_html=True)
            st.caption("Dot = current price · green side = nearer the goal · 🛡️ = can no longer lose")

    with c_done:
        col_header("done", "🏁 Finished", len(perf_eq_df) + len(perf_opt_df))
        fin_rows = []
        for _, r in (perf_eq_df.iterrows() if not perf_eq_df.empty else []):
            fin_rows.append((r["exit_date"], r["ticker"], r["status"], float(r["pnl_pct"]), f"closed {r['exit_date']}"))
        for _, r in (perf_opt_df.iterrows() if not perf_opt_df.empty else []):
            fin_rows.append((r["exit_date"], "📜 " + option_short_name(r["option_contract"]), r["status"], float(r["pnl_pct"]), f"option · closed {r['exit_date']}"))
        fin_rows.sort(key=lambda x: str(x[0]), reverse=True)
        if not fin_rows:
            empty_box("Finished trades appear here.")
        else:
            if len(perf_opt_df):
                st.caption(f"{len(perf_eq_df)} share trades · {len(perf_opt_df)} option ideas")
            for _, tk, stt, pp, when in fin_rows[:8]:
                st.markdown(finished_row_html(tk, stt, pp, when), unsafe_allow_html=True)

    if stats.get("scanned"):
        sec_title("🌡️", "Market pulse", "the whole NSE, from this check")
        n_sc = int(stats.get("scanned", 0))
        up_pct = stats.get("uptrend", 0) / max(1, n_sc) * 100.0
        n20 = float(stats.get("nifty_20d", 0.0) or 0.0)
        score_cards([
            ("Shares on the NSE", f"{stats.get('listed', 0):,}", "every normal (EQ) share", "", ""),
            ("Easy to trade", f"{n_sc:,}", f"{stats.get('illiquid', 0):,} too thinly traded · {stats.get('wild', 0):,} too risky", "", ""),
            ("Market mood", f"{up_pct:.0f}% rising", breadth_word(up_pct), "good" if up_pct >= 55 else ("bad" if up_pct < 40 else ""), "pos" if up_pct >= 50 else "neg"),
            ("Nifty, last 20 days", f"{n20:+.1f}%", "the market as a whole", "good" if n20 > 0 else ("bad" if n20 < 0 else ""), "pos" if n20 >= 0 else "neg"),
        ])
        p1, p2 = st.columns([1.1, 1])
        with p1:
            st.markdown("**How today's picks were found**")
            healthy = max(0, int(stats.get("uptrend", 0)) - int(stats.get("overbought", 0)) - int(stats.get("quiet", 0)))
            st.markdown(funnel_html([
                ("All NSE shares", stats.get("listed", 0)),
                ("Prices available", stats.get("priced", 0)),
                ("Easy & safe to trade", n_sc),
                ("Going up (uptrend)", stats.get("uptrend", 0)),
                ("Not overheated, normal volume", healthy),
                ("Strong AI score", stats.get("candidates", 0)),
                ("Finalists, fully checked", stats.get("deep", 0)),
                ("Passed every check", stats.get("qualified", 0)),
            ]), unsafe_allow_html=True)
            st.caption(f"{stats.get('held', 0)} rising shares were left out because you already own them, sold them in the last {COOLDOWN_CALENDAR_DAYS} days, or their sector is full.")
        with p2:
            st.markdown("**How much of the market is rising**")
            st.markdown(gauge_html(up_pct), unsafe_allow_html=True)
            st.caption("Share of easy-to-trade NSE shares that are above their 20-day average with steady momentum. Picks work better when most of the market is rising.")
            heat = sector_heat_html(stats.get("sector_breadth", {}) or {})
            if heat:
                st.markdown("**Strongest sectors right now**")
                st.markdown(heat, unsafe_allow_html=True)
        if stats.get("leaders"):
            st.markdown("**Beating the Nifty most over 20 days** · for information, not buy tips")
            st.markdown(leaders_html(stats["leaders"]), unsafe_allow_html=True)

    sec_exp = get_active_sector_exposure()
    if sec_exp:
        with st.expander("📊 Open trades by sector"):
            chart_sector(sec_exp)
    if not df_res.empty:
        with st.expander(f"🔍 The {len(df_res)} finalists — and why each did or didn't pass"):
            diag = df_res.rename(columns={"Ticker": "Stock", "AI Win Confidence": "AI score", "Rejection Reason": "Result", "Adjusted Score": "Final score"})
            cols_ = ["Stock", "Sector", "Price (₹)", "Expected Return", "Final score", "Vs Nifty (20d)", "Easy to trade (₹cr/day)", "Qualified", "Result"]
            st.dataframe(diag[[c for c in cols_ if c in diag.columns]], width="stretch", hide_index=True)

# ==============================================================================
# 18. TAB: RESULTS
# ==============================================================================
with tab_results:
    n_ = perf["n"]
    if n_ == 0:
        sec_title("📈", "Results")
        score_cards([("Share profit/loss", "₹0", "no finished share trades yet", "", ""), ("Win rate", "–", "needs finished share trades", "", ""),
                     ("Finished share trades", "0", (f"+ {len(perf_opt_df)} option idea{'s' if len(perf_opt_df) != 1 else ''} → 🎲 Options tab" if len(perf_opt_df) else "come back soon"), "", ""),
                     ("Open right now", str(n_open_all), f"{open_split_text()} · {inr(open_unreal_all)} so far", "good" if open_unreal_all > 0 else ("bad" if open_unreal_all < 0 else ""), "")])
        callout("info", "⏳", "<b>No share trade has finished yet.</b> Once the first ones reach their goal or safety exit, your honest results appear here."
                + (" Option results are on the 🎲 Options tab." if len(perf_opt_df) or len(active_opt) else ""))
    else:
        exp_, net = perf["expectancy_pct"], perf["net_inr"]
        sec_title("📈", "Results", "finished share trades, after all costs")
        score_cards([
            ("Share profit/loss", inr(net), f"{inr(perf['expectancy_inr'])} per trade on average", "good" if net > 0 else ("bad" if net < 0 else ""), "pos" if net > 0 else ("neg" if net < 0 else "")),
            ("Win rate", f"{perf['target_hit_rate']:.0f}%", f"{perf['wins']} won · {perf['losses']} lost · {perf['be']} saved · {perf['expired']} timed out", "", ""),
            ("Finished share trades", str(n_), f"likely win rate {perf['ci_low']:.0f}–{perf['ci_high']:.0f}%" + (f" · + {len(perf_opt_df)} option idea{'s' if len(perf_opt_df) != 1 else ''} → 🎲 Options" if len(perf_opt_df) else ""), "", ""),
            ("Open right now", str(n_open_all), f"{open_split_text()} · {inr(open_unreal_all)} so far · 🛡️ {be_prot} can't lose", "good" if open_unreal_all > 0 else ("bad" if open_unreal_all < 0 else ""), ""),
        ])
        if n_ < 30:
            callout("warn", "⏳", f"<b>Too early to judge.</b> Only {n_} trades have finished. Results this early can be pure luck.")
        elif exp_ > 0.3:
            callout("good", "✅", f"<b>Looking positive.</b> On average each trade made <b>{exp_:+.2f}%</b> after costs ({n_} trades).")
        elif exp_ > -0.3:
            callout("info", "➖", f"<b>Roughly break-even.</b> On average <b>{exp_:+.2f}%</b> per trade after costs ({n_} trades).")
        else:
            callout("bad", "⚠️", f"<b>Losing money after costs.</b> On average <b>{exp_:+.2f}%</b> per trade ({n_} trades). Don't use real money.")
        ch1, ch2 = st.columns([3, 2])
        with ch1:
            chart_cumulative(perf_eq_df, "Your running profit/loss (₹) — going up is good", key="cum_shares")
        with ch2:
            chart_outcomes(perf, key="outcomes_shares")

        sec_title("✨", "Highlights")
        best_i, worst_i = perf_eq_df["pnl_pct"].idxmax(), perf_eq_df["pnl_pct"].idxmin()
        sk_n, sk_win = current_streak(perf_eq_df)
        dh = [(b - a).days for a, b in zip(perf_eq_df["entry_date"], perf_eq_df["exit_date"]) if a is not None and b is not None]
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            stat_card("🏆 Best trade", f"{perf_eq_df.loc[best_i, 'ticker']} {perf_eq_df.loc[best_i, 'pnl_pct']:+.1f}%", "after costs")
        with s2:
            stat_card("📉 Worst trade", f"{perf_eq_df.loc[worst_i, 'ticker']} {perf_eq_df.loc[worst_i, 'pnl_pct']:+.1f}%", "after costs")
        with s3:
            stat_card("🔥 Current streak", f"{sk_n} {'profitable' if sk_win else 'unprofitable'}" if sk_n else "–", "trades in a row")
        with s4:
            stat_card("⏱️ Typical hold", f"{np.mean(dh):.1f} days" if dh else "–", "buy to sell")

        nifty_s = get_nifty_close_series()
        first_d = perf_eq_df["entry_date"].dropna().min()
        nifty_since = nifty_return_between(nifty_s, first_d, ist_now().date()) if first_d else None
        excess = [float(r["pnl_pct"]) - x for _, r in perf_eq_df.iterrows() for x in [nifty_return_between(nifty_s, r["entry_date"], r["exit_date"])] if x is not None]
        if nifty_since is not None:
            ex_ = float(np.mean(excess)) if excess else None
            beat = ("" if ex_ is None else (f" On the same days, your trades <b>beat the market by {ex_:.2f}%</b> per trade on average." if ex_ >= 0
                                            else f" On the same days, your trades <b>trailed the market by {abs(ex_):.2f}%</b> per trade on average."))
            callout("info", "🇮🇳", f"<b>Compared with the market:</b> the Nifty 50 moved <b>{nifty_since:+.2f}%</b> since your first trade.{beat}")
        with st.expander("🔍 More detail"):
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Average winning trade", inr(perf["avg_win"]))
            b2.metric("Average losing trade", inr(perf["avg_loss"]))
            b3.metric("Money won ÷ money lost", f"{perf['profit_factor']:.2f}" if perf["profit_factor"] is not None else "no losses yet")
            b4.metric("Biggest drop from a peak", inr(perf["max_dd"]))
            chart_hist(perf_eq_df)
            sg = sector_stats(perf_eq_df)
            if not sg.empty:
                st.dataframe(sg.rename(columns={"n": "Trades", "avg": "Average %", "win_rate": "Win rate %"})[["Sector", "Trades", "Average %", "Win rate %"]].round(1)
                             .sort_values("Average %", ascending=False), width="stretch", hide_index=True)

    sec_title("🔎", "All trades")
    master = []
    if not df_eq.empty:
        d = df_eq.copy()
        d["Sector"] = d.apply(lambda x: get_trade_sector(x["ticker"], x["features_json"]), axis=1)
        d["Type"] = "Share"
        d["_o"] = d["status"].apply(classify_outcome)
        d["Result"] = d.apply(lambda x: friendly_status(x["status"], is_stop_breakeven_protected(float(x["entry_price"]), float(x["stop_loss"]))), axis=1)
        master.append(d.rename(columns={"date_str": "Date", "ticker": "Stock", "entry_price": "Bought at (₹)", "target_price": "Goal (₹)", "stop_loss": "Safety exit (₹)",
                                        "latest_price": "Price now (₹)", "pnl_pct": "Profit/Loss (%)", "last_audited": "Last checked"}))
    if not df_opt.empty:
        d = df_opt.copy()
        d["Type"] = d["option_contract"].apply(lambda c: "Option (live quote)" if is_live_option(c) else "Option (model price)")
        d["Sector"] = d["share_name"].apply(get_ticker_sector)
        d["_o"] = d["status"].apply(classify_outcome)
        d["Result"] = d["status"].apply(friendly_status)
        master.append(d.rename(columns={"date_key": "Date", "option_contract": "Stock", "entry_premium": "Bought at (₹)", "current_option_price": "Price now (₹)",
                                        "target_premium": "Goal (₹)", "stop_loss_premium": "Safety exit (₹)", "pnl_pct": "Profit/Loss (%)", "last_audited": "Last checked"}))
    if master:
        keep = ["Date", "Stock", "Sector", "Type", "Bought at (₹)", "Goal (₹)", "Safety exit (₹)", "Price now (₹)", "Profit/Loss (%)", "Result", "Last checked", "_o"]
        mdf = pd.concat([m[[c for c in keep if c in m.columns]] for m in master], ignore_index=True).sort_values("Date", ascending=False, kind="stable").reset_index(drop=True)
        f1, f2, f3, f4 = st.columns(4)
        lmap = {"Open": "ACTIVE", "Won": "WIN", "Lost": "LOSS", "Saved (no loss)": "BE", "Closed after 7 days": "EXPIRED"}
        ch_status = f1.multiselect("Result", list(lmap.keys()), default=list(lmap.keys()))
        t_opts = sorted(mdf["Type"].unique())
        ch_types = f2.multiselect("Type", t_opts, default=t_opts)
        s_opts = sorted(mdf["Sector"].astype(str).unique())
        ch_sectors = f3.multiselect("Sector", s_opts, default=s_opts)
        search = f4.text_input("Search a stock", "")
        view = mdf[mdf["_o"].isin({lmap[s] for s in ch_status}) & mdf["Type"].isin(ch_types) & mdf["Sector"].astype(str).isin(ch_sectors)]
        if search.strip():
            view = view[view["Stock"].astype(str).str.contains(search.strip(), case=False, na=False)]
        view = view.drop(columns=["_o"])
        st.caption(f"Showing **{len(view)}** of {len(mdf)}. Share prices in ₹. Click a column heading to sort.")
        st.dataframe(style_ledger(view), width="stretch", hide_index=True)
        st.download_button("⬇️ Download as spreadsheet (CSV)", view.to_csv(index=False).encode("utf-8"), "my_trades.csv", "text/csv")
    else:
        st.info("No trades yet.")

# ==============================================================================
# 19. TAB: WHAT THE AI LEARNED
# ==============================================================================
with tab_learn:
    n_ = perf["n"]
    decided_n = int(perf_eq_df["outcome"].isin(["WIN", "LOSS"]).sum()) if not perf_eq_df.empty else 0
    level = "🌱 Rookie" if n_ < 30 else ("📚 Learning" if n_ < 100 else "🎓 Experienced")
    sec_title("🧠", "What the AI has learned", "in plain English")
    st.markdown(_collapse(
        f'<div class="stat"><div class="l">Experience level</div><div class="v">{level} — {n_} finished trades</div>'
        f'<div class="meter"><div style="width:{min(100.0, decided_n / MIN_SETTLED_TO_RETRAIN * 100.0):.0f}%"></div></div>'
        f'<div class="sub2">{decided_n} of {MIN_SETTLED_TO_RETRAIN} clear win/loss trades needed before the AI may consider upgrading itself.</div></div>'), unsafe_allow_html=True)
    if n_ == 0:
        callout("info", "🌱", "<b>The AI hasn't finished any trades yet</b>, so it has nothing to learn from. After a few trades close you'll see lessons, strengths and weak spots here.")
    else:
        sec_title("💬", "What I've noticed")
        ins = build_insights(perf_eq_df)
        for line in (ins or ["Not enough trades yet to spot patterns."]):
            st.markdown(f"- {line}")
        sec_title("📰", "Latest lessons", "most recent finished trades")
        for _, r in perf_eq_df.tail(5).iloc[::-1].iterrows():
            o_ = classify_outcome(r["status"])
            lesson = {"WIN": "Small confidence boost for this stock for a while.", "LOSS": "More cautious about this stock for a while.",
                      "BE": "The safety exit protected the money. No change.", "EXPIRED": "Timed out — nothing learned."}.get(o_, "")
            st.markdown(_collapse(f'<div class="lesson"><b>{esc(r["ticker"])}</b> · {esc(friendly_status(r["status"]))} · '
                                  f'<b class="{"pos" if r["pnl_pct"] >= 0 else "neg"}">{float(r["pnl_pct"]):+.1f}%</b><br><span class="sub2">{esc(lesson)}</span></div>'), unsafe_allow_html=True)
        sec_title("🏭", "Where it's working and where it isn't", "by sector (3+ trades)")
        sg = sector_stats(perf_eq_df)
        sg3 = sg[sg["n"] >= 3] if not sg.empty else sg
        lc, rc = st.columns(2)
        with lc:
            st.markdown("**✅ Working well**")
            good = sg3[sg3["avg"] > 0].sort_values("avg", ascending=False).head(5) if not sg3.empty else sg3
            if len(good):
                st.markdown(hbars_html([(r["Sector"], float(r["avg"]), f"{r['avg']:+.1f}% · {int(r['n'])} trades") for _, r in good.iterrows()]), unsafe_allow_html=True)
            else:
                st.caption("No sector has a clear profit yet.")
        with rc:
            st.markdown("**⚠️ Struggling**")
            bad = sg3[sg3["avg"] < 0].sort_values("avg").head(5) if not sg3.empty else sg3
            if len(bad):
                st.markdown(hbars_html([(r["Sector"], float(r["avg"]), f"{r['avg']:+.1f}% · {int(r['n'])} trades") for _, r in bad.iterrows()]), unsafe_allow_html=True)
            else:
                st.caption("No sector is clearly losing (or not enough trades yet).")
        sec_title("📈", "Is it getting better?", "win rate of the last 10 trades")
        chart_rolling(perf_eq_df, 10)
        sec_title("🧷", "Stocks it remembers", "lessons fade by half every 30 days")
        careful, liked = compute_memory(closed_all)
        m1, m2 = st.columns(2)
        with m1:
            st.markdown("**🚧 Extra careful with**")
            if careful:
                st.markdown(hbars_html([(t, -float(p), f"-{p} pts · ~{d}d left") for t, p, d in careful[:6]]), unsafe_allow_html=True)
            else:
                st.caption("None right now.")
        with m2:
            st.markdown("**👍 Slightly more confident about**")
            if liked:
                st.markdown(hbars_html([(t, float(p), f"+{p} pts · ~{d}d left") for t, p, d in liked[:6]]), unsafe_allow_html=True)
            else:
                st.caption("None right now.")

    sec_title("🎚️", "Can you trust its confidence number?")
    cal_rows = calibration_rows(closed_all)
    if len(cal_rows) >= 10:
        said, actual = float(np.mean([c for c, _ in cal_rows])), float(np.mean([h for _, h in cal_rows]) * 100.0)
        kind = "warn" if actual < said - 8 else "good"
        verdict = "it has been overconfident" if actual < said - 8 else ("it has been a little modest" if actual > said + 8 else "its confidence is a fair guide")
        callout(kind, "🎚️", f"On average the AI said it was <b>{said:.0f}% sure</b> and was right <b>{actual:.0f}%</b> of the time — {verdict}.")
        chart_calibration(cal_rows)
    else:
        st.info("This appears once at least 10 trades have clearly won or lost.")

    sec_title("🔧", "The AI itself")
    try:
        rstate = json.loads(get_meta("retrain_state", "{}") or "{}")
    except Exception:
        rstate = {}
    cal = st.session_state.get("cal_info")
    st.markdown(
        f"- **Version in use:** {st.session_state.get('ml_model_source', 'not loaded yet')}\n"
        f"- **Last upgrade:** {rstate.get('last_promoted', 'never — still the original')}\n"
        f"- **Upgrade rule:** only after {MIN_SETTLED_TO_RETRAIN}+ clear win/loss trades, and only if the new version tests better. Your original model file is never overwritten."
        + (f"\n- **Confidence tuning:** {cal['mode']}." if cal else "")
        + "\n- **Options:** this AI learns from share trades only. Options have their own separate learner — see the 🎲 Options tab.")
    with st.expander("How the AI picks a share"):
        st.markdown(
            "1. Downloads prices for **every share on the NSE** (normal 'EQ' series), skipping ones that are thinly traded, under ₹10, stuck at their price limit or extremely jumpy.\n"
            "2. Keeps shares in a steady uptrend that aren't overheated and are trading normally.\n"
            f"3. The AI model scores how likely each is to reach its goal before its safety exit; the best {MAX_DEEP_CHECKS} become finalists.\n"
            "4. Finalists get a **fresh price**, and their score is corrected using your real results, then nudged by past lessons, the market and the news check.\n"
            "5. Shares with bad news (SEBI notices, pledges, defaults and similar) are blocked.\n"
            f"6. Only shares with **{MIN_CALIBRATED_CONFIDENCE:.0f}%+ confidence**, an uptrend, normal trading volume and at least **{MIN_NET_RETURN_PCT:.1f}% possible profit after costs** are kept.")

# ==============================================================================
# 20. TAB: OPTIONS
# ==============================================================================
with tab_options:
    sec_title("🎲", "Options idea of the day", "paper trading")
    st.caption(f"One call-option idea a day under {inr(OPTION_BUDGET_INR)}. Nothing is bought for real, and it is **not** counted in your share results.")
    if opt_signal:
        live_src = is_live_option(opt_signal["option_contract"])
        with st.container(border=True):
            st.markdown(f'<span class="tag {"good" if live_src else "warn"}">{"Live NSE quote" if live_src else "Estimated price — live quote unavailable"}</span>', unsafe_allow_html=True)
            o1, o2, o3 = st.columns(3)
            with o1:
                st.metric("Idea", option_short_name(opt_signal["option_contract"]), help="A 'CE' (call) option gains value if the share price rises.")
                st.markdown(f"Share price when picked: **₹{float(opt_signal['underlying_spot']):,.2f}**")
            with o2:
                st.metric("Sell price now", f"₹{float(opt_signal['current_option_price']):.2f}", f"{float(opt_signal['pnl_pct']):+.2f}% vs cost (₹{float(opt_signal['entry_premium']):.2f})")
                st.markdown(f"Expected wobble: **{opt_signal['implied_vol']}%** a year")
            with o3:
                st.metric("Cost to buy", inr(opt_signal["total_capital"]), f"{int(float(opt_signal['lot_size']))} shares in one lot", delta_color="off")
                st.markdown(f"Trend strength: **{opt_signal['ai_confidence']}** (not a chance of winning)")
            m1, m2, m3 = st.columns(3)
            m1.info(f"🎯 Goal: ₹{float(opt_signal['target_premium']):.2f} (+{(OPTION_TARGET_MULT - 1) * 100:.0f}%)")
            m2.warning(f"🛑 Safety exit: ₹{float(opt_signal['stop_loss_premium']):.2f} (-{(1 - OPTION_STOP_MULT) * 100:.0f}%)")
            m3.success(f"Status: **{friendly_status(opt_signal['status'])}**")
            if not live_src:
                st.caption("The price comes from a maths formula (Black-Scholes) because NSE's live quote wasn't available. It is only an estimate.")
    else:
        st.info(st.session_state.get("opt_note") or "No option idea today.")
    if not state["trading_day"]:
        st.caption(f"NSE is closed today ({state['reason']}), so no new idea is created.")

    # ---------- scoreboard ----------
    sec_title("📊", "Options scoreboard", "finished option ideas, kept apart from your share results")
    if perf_o["n"] == 0:
        score_cards([("Options profit/loss", "₹0", "no finished ideas yet", "", ""), ("Win rate", "–", "needs finished ideas", "", ""),
                     ("Finished ideas", "0", "one idea a day at most", "", ""), ("Open right now", str(len(active_opt)), f"{inr(opt_open_unreal)} so far", "", "")])
        callout("info", "⏳", "<b>No option idea has finished yet.</b> Each one closes at its goal, its safety exit, or after 7 days.")
    else:
        net_o = perf_o["net_inr"]
        score_cards([
            ("Options profit/loss", inr(net_o), f"{inr(perf_o['expectancy_inr'])} per idea on average", "good" if net_o > 0 else ("bad" if net_o < 0 else ""), "pos" if net_o > 0 else ("neg" if net_o < 0 else "")),
            ("Win rate", f"{perf_o['target_hit_rate']:.0f}%", f"{perf_o['wins']} won · {perf_o['losses']} lost · {perf_o['expired']} timed out", "", ""),
            ("Finished ideas", str(perf_o["n"]), f"likely win rate {perf_o['ci_low']:.0f}–{perf_o['ci_high']:.0f}%", "", ""),
            ("Open right now", str(len(active_opt)), f"{inr(opt_open_unreal)} so far", "good" if opt_open_unreal > 0 else ("bad" if opt_open_unreal < 0 else ""), ""),
        ])
        if perf_o["n"] < 20:
            callout("warn", "⏳", f"<b>Too early to judge.</b> Only {perf_o['n']} option idea{'s have' if perf_o['n'] != 1 else ' has'} finished. Options swing a lot, so early results are mostly luck.")
        oc1, oc2 = st.columns([3, 2])
        with oc1:
            chart_cumulative(perf_opt_df, "Options running profit/loss (₹)", key="cum_options")
        with oc2:
            chart_outcomes(perf_o, title="How your option ideas ended", key="outcomes_options")
        lv, md_ = opt_learn.get("live"), opt_learn.get("model")
        if lv or md_:
            q1, q2 = st.columns(2)
            with q1:
                stat_card("📡 Ideas with a live NSE price", f"{lv['avg']:+.1f}% average" if lv else "none yet",
                          f"{lv['n']} finished · {lv['win_rate']:.0f}% won" if lv else "NSE's quote wasn't reachable for any idea so far")
            with q2:
                stat_card("🧮 Ideas with an estimated price", f"{md_['avg']:+.1f}% average" if md_ else "none yet",
                          f"{md_['n']} finished · {md_['win_rate']:.0f}% won · results are estimates too" if md_ else "every idea so far had a live quote")

    # ---------- open options ----------
    sec_title("🟢", "Open option ideas", f"{len(active_opt)} running")
    if active_opt.empty:
        empty_box("No option ideas are open right now.")
    else:
        oc = st.columns(min(3, len(active_opt)))
        for i_, (_, o) in enumerate(active_opt.iterrows()):
            with oc[i_ % len(oc)]:
                st.markdown(option_mini_html(o), unsafe_allow_html=True)

    # ---------- the options learner ----------
    sec_title("🧠", "What the options learner has learned", "separate from the share AI")
    rn = int(opt_learn.get("recent_n", 0))
    st.markdown(_collapse(
        f'<div class="stat"><div class="l">Options experience</div><div class="v">{opt_learn["n"]} finished idea{"s" if opt_learn["n"] != 1 else ""}</div>'
        f'<div class="meter"><div style="width:{min(100.0, rn / OPT_LEARN_MIN_TRADES * 100.0):.0f}%"></div></div>'
        f'<div class="sub2">{min(rn, OPT_LEARN_MIN_TRADES)} of {OPT_LEARN_MIN_TRADES} recent ideas (last {OPT_LEARN_WINDOW_DAYS} days) needed before it may become pickier. '
        f'Until then only the “rest a share after a loss” rule is used.</div></div>'), unsafe_allow_html=True)
    rules = []
    if opt_learn["rest"]:
        rules.append("🚧 <b>Taking a break from:</b> " + ", ".join(f"{esc(k)} (~{v} day{'s' if v != 1 else ''} left)" for k, v in sorted(opt_learn["rest"].items(), key=lambda x: -x[1]))
                     + " — their recent options lost money. The break fades by itself.")
    if opt_learn["bar"] > 60.0:
        rise = trend_bar_to_rise(opt_learn["bar"])
        rise_txt = f"a 5-day rise of at least <b>{rise:+.1f}%</b>" if rise > 0 else f"trend strength {opt_learn['bar']:.0f}+"
        rules.append(f"📈 <b>Pickier:</b> recent ideas lost <b>{opt_learn['wavg']:+.1f}%</b> on average, so it now needs {rise_txt} before it picks a share.")
    elif opt_learn["ready"]:
        rules.append(f"✅ <b>Normal pickiness:</b> recent ideas made <b>{opt_learn['wavg']:+.1f}%</b> on average, so it isn't adding extra filters.")
    if opt_learn["live_only"]:
        rules.append("📡 <b>Real prices only:</b> ideas priced with the maths formula have been losing, so it now only takes ideas with a real NSE quote.")
    if rules:
        for r_ in rules:
            callout("info", "🧠", r_)
    else:
        callout("info", "🌱", "<b>No rules in force yet.</b> It hasn't seen enough finished option ideas to change anything.")
    if opt_learn.get("by_share"):
        bs = sorted(((k, v) for k, v in opt_learn["by_share"].items() if v), key=lambda x: -x[1]["avg"])
        st.markdown(hbars_html([(k, float(v["avg"]), f"{v['avg']:+.1f}% · {v['n']} idea{'s' if v['n'] != 1 else ''}") for k, v in bs[:9]]), unsafe_allow_html=True)
        st.caption("Average result of finished option ideas, per share.")

    # ---------- every option idea ----------
    if not df_opt.empty:
        sec_title("📜", "Every option idea")
        od = df_opt.copy()
        od["Price source"] = od["option_contract"].apply(lambda c: "Live NSE quote" if is_live_option(c) else "Estimated")
        od["Contract"] = od["option_contract"].apply(option_short_name)
        od["Result"] = od["status"].apply(friendly_status)
        od = od.rename(columns={"date_key": "Date", "entry_premium": "Bought at (₹)", "current_option_price": "Price now (₹)", "target_premium": "Goal (₹)",
                                "stop_loss_premium": "Safety exit (₹)", "pnl_pct": "Profit/Loss (%)", "ai_confidence": "Trend strength", "last_audited": "Last checked"})
        ocols = ["Date", "Contract", "Price source", "Bought at (₹)", "Goal (₹)", "Safety exit (₹)", "Price now (₹)", "Profit/Loss (%)", "Trend strength", "Result", "Last checked"]
        st.dataframe(style_ledger(od[[c for c in ocols if c in od.columns]]), width="stretch", hide_index=True)

    with st.expander("How the options idea works"):
        st.markdown(
            "- It looks at the biggest F&O shares and picks the one with the strongest 5-day climb (skipping any with bad news or a recent trade).\n"
            "- The contract is a **call about 2% above the share price**, expiring on the **last Tuesday of the month** (NSE's stock-option expiry day).\n"
            "- If NSE gives a real buy/sell quote with a tight gap and real trading, that's used. Otherwise a maths-formula price is used and clearly labelled.\n"
            "- You \"buy\" at the higher asking price and are valued at the lower selling price, so every trade starts slightly down.\n"
            f"- Goal +{(OPTION_TARGET_MULT - 1) * 100:.0f}%, safety exit -{(1 - OPTION_STOP_MULT) * 100:.0f}%, and it closes after {MAX_HOLD_CALENDAR_DAYS} days anyway.\n"
            "- **The options learner** only learns from finished option ideas and only changes the options idea — never the share AI. "
            "It rests a share after a recent option loss, gets pickier about the 5-day climb if recent ideas lose on average, and can insist on real NSE prices. "
            f"Its stricter rules only look at the last {OPT_LEARN_WINDOW_DAYS} days, so they lift by themselves and get re-tested.\n"
            "- Lot sizes come from a built-in table. **NSE changes them from time to time — please check them against NSE's current list.**")

st.markdown('<div class="disc">Practice tool using pretend money. Not investment advice. Past results don’t predict future results.</div>', unsafe_allow_html=True)