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
import shutil
import requests

import streamlit as st
import pandas as pd
import numpy as np
import joblib
import pytz
import duckdb
from quant_guardrails import (CAPITAL_LIMIT_INR, EQUITY_TARGET_INR, OPTION_MAX_SINGLE_INR,
    OPTION_TOTAL_LIMIT_INR, allowable_equity_shares, can_allocate_option,
    review_long_option, remaining_capital)

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

warnings.filterwarnings("ignore")

# set_page_config must be the first Streamlit command that renders anything
st.set_page_config(
    page_title="NSE AI • Bright Research Desk",
    page_icon="✨",
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
POSITION_SIZE_INR = EQUITY_TARGET_INR
MAX_DAILY_EQUITY_TRADES = 10
MAX_HOLD_CALENDAR_DAYS = 7
BREAKEVEN_TRIGGER_RATIO = 0.65
MAX_ACTIVE_PER_SECTOR = 2
MAX_ACTIVE_OTHER_SECTOR = 4            # shares we couldn't classify share one bigger bucket
MAX_OPEN_POSITIONS = 10
MAX_OPEN_RISK_INR = 30000.0
COOLDOWN_CALENDAR_DAYS = 3
HALF_LIFE_DAYS = 30.0
# Baseline from the previously displayed 20 active share records. Not a fabrication of records.
# Configure HISTORY_EXPECTED_MIN_SHARES=0 only after explicitly accepting a fresh start.
HISTORY_EXPECTED_MIN_SHARES = 20
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
MIN_CALIBRATED_CONFIDENCE = 60.0       # (kept for older saved trades; picks are now chosen on expected value — see MIN_EV_PCT)
MIN_EV_PCT = 0.2                       # a pick must be expected to make at least this much after ALL costs (in % per trade)
LEARNING_PICKS_PER_DAY = 0  # do not paper-buy rejected ideas just to train             # while still learning, at most this many clearly-marked 🧪 learning picks a day
CAL_PRIOR_TRADES = 20                  # the confidence correction starts near 50% and moves to your real win rate as results arrive
MIN_RVOL_THRESHOLD = 0.45
MIN_NET_RETURN_PCT = 1.10
MIN_PRICE_INR = 0.01  # eligible at any price with liquidity safeguards
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
OPTION_BUDGET_INR = OPTION_MAX_SINGLE_INR
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
MIN_SETTLED_TO_RETRAIN = 100
RETRAIN_STEP_INTERVAL = 25
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
from schema_migrations import ensure_schema, SCHEMA_VERSION

def init_duckdb_storage():
    """Safely migrate in-place; never silently ignore a failed schema upgrade."""
    with DB_LOCK:
        return ensure_schema(DB_PATH, ROOT_DIR / "backups")

try:
    DB_SCHEMA_STATUS = init_duckdb_storage()
except Exception:
    import logging
    logging.exception("DuckDB schema upgrade failed")
    st.error("The saved trading database could not be upgraded safely. No trading history was intentionally deleted. Check Streamlit Cloud logs and restore the backed-up database if necessary.")
    st.stop()

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

def get_history_integrity() -> dict:
    """Do not mark a newly empty local file as restored, or spend missing paper capital."""
    try:
        local_count = journal_counts()["total"]
    except Exception:
        local_count = 0
    cloud_info = st.session_state.get("hydrate_info") or {}
    return history_state(
        local_count,
        HISTORY_EXPECTED_MIN_SHARES,
        cloud_configured=bool(supabase),
        cloud_shares=cloud_info.get("remote") if supabase and cloud_info else None,
        cloud_failed=bool(cloud_info.get("fetch_failed", False)),
    )

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
            con.execute("INSERT OR REPLACE INTO system_metadata (meta_key, meta_val) VALUES (?, ?)", [key, str(val)])
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
def _secret(name: str, default="") -> str:
    try:
        return str(st.secrets.get(name, os.environ.get(name, default)))
    except Exception:
        return str(os.environ.get(name, default))

from history_integrity import history_state

# Streamlit Cloud can override the baseline without committing any credentials.
try:
    HISTORY_EXPECTED_MIN_SHARES = max(0, int(_secret("HISTORY_EXPECTED_MIN_SHARES", str(HISTORY_EXPECTED_MIN_SHARES))))
except ValueError:
    HISTORY_EXPECTED_MIN_SHARES = 20

def check_password() -> bool:
    if st.session_state.get("password_correct"):
        return True
    expected_user = _secret("AUTH_USER")
    expected_hash = _secret("AUTH_PASS_HASH")
    if not expected_user or not expected_hash:
        st.error("Please set AUTH_USER and AUTH_PASS_HASH in Streamlit secrets before starting.")
        return False
    if time.time() < st.session_state.get("auth_lockout_until", 0):
        st.error("Login temporarily locked after repeated attempts.")
        return False
    st.title("🔐 NSE Quant Research")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign in")
    if submit:
        ok = hmac.compare_digest(username.strip(), expected_user.strip()) and hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest(), expected_hash.strip().lower())
        if ok:
            st.session_state["password_correct"] = True
            st.session_state["failed_auth_attempts"] = 0
            st.rerun()
        n = st.session_state.get("failed_auth_attempts", 0) + 1
        st.session_state["failed_auth_attempts"] = n
        if n >= 5:
            st.session_state["auth_lockout_until"] = time.time() + 900
        st.error("Incorrect credentials")
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
    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_KEY")
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
    st.session_state.setdefault("supabase_fetch_errors", {})
    st.session_state["supabase_fetch_errors"].pop(table_name, None)
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
            st.session_state.setdefault("supabase_fetch_errors", {})[table_name] = type(e).__name__
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
    info = {"remote": 0, "remote_options": 0, "remote_active": 0, "restored": 0, "kept_local": 0, "bad_rows": 0, "first_error": ""}
    st.session_state["hydrate_info"] = info
    if not supabase:
        return
    ist_now_str = ist_now().strftime('%Y-%m-%d %H:%M:%S')
    eq_data = fetch_all_supabase_rows("predictions")
    opt_data = fetch_all_supabase_rows("options_journal")
    info["fetch_failed"] = bool(st.session_state.get("supabase_fetch_errors", {}).get("predictions"))
    info["remote"] = len(eq_data or [])
    info["remote_options"] = len(opt_data or [])
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
                        (date_key, timestamp, share_name, option_contract, strike_price, expiry_days,
                         underlying_spot, lot_size, entry_premium, current_option_price, target_premium,
                         stop_loss_premium, total_capital, ai_confidence, implied_vol, status, pnl_pct, last_audited)
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


def sync_local_history_to_supabase(batch_size: int = 200, offset: int = 0) -> dict:
    """Upload missing old records without overwriting existing cloud rows.

    Uses a resumable offset for large histories. This is best-effort only; verify
    cloud totals before trusting the remote database as your durable backup.
    """
    if not supabase:
        raise RuntimeError("Add SUPABASE_URL and SUPABASE_KEY to Streamlit Secrets first")
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            eq_rows = con.execute("SELECT * FROM trade_journal ORDER BY date_str DESC NULLS LAST").df().to_dict("records")
            opt_rows = con.execute("SELECT * FROM daily_options_journal ORDER BY date_key DESC NULLS LAST").df().to_dict("records")
        finally:
            con.close()
    records = [("share", r) for r in eq_rows] + [("option", r) for r in opt_rows]
    start = max(0, min(int(offset), len(records)))
    end = min(len(records), start + max(1, int(batch_size)))
    stats = {"shares_inserted": 0, "options_inserted": 0, "already_remote": 0,
             "failed": 0, "errors": [], "more_to_check": False, "next_offset": start,
             "total_rows": len(records)}
    first_failed = None
    for i in range(start, end):
        kind, row = records[i]
        try:
            if kind == "share":
                ticker = str(row.get("ticker") or "").strip()
                dt = str(row.get("date_str") or "")[:10]
                if not ticker or len(dt) != 10:
                    stats["next_offset"] = i + 1
                    continue
                prior = supabase.table("predictions").select("id").eq("ticker",ticker).eq("predicted_date",dt).limit(1).execute()
                if prior.data:
                    stats["already_remote"] += 1
                else:
                    entry = _num(row.get("entry_price"))
                    payload = {
                        "predicted_date": dt, "ticker": ticker,
                        "company_name": f"{ticker} (restored paper history)",
                        "entry_price": entry, "target_price": _num(row.get("target_price")),
                        "stop_loss": _num(row.get("stop_loss")),
                        "position_gbp": _num(row.get("capital_allocated")),
                        "shares_qty": _int(row.get("shares")),
                        "status": str(row.get("status") or "UNKNOWN"),
                        "latest_price": _num(row.get("latest_price"),entry),
                        "pnl_pct": _num(row.get("pnl_pct")),
                        "features_json": _parse_features(row.get("features_json")),
                        "last_checked": str(row.get("last_audited") or row.get("timestamp") or f"{dt} 15:30:00")[:19],
                    }
                    supabase.table("predictions").insert(payload).execute()
                    stats["shares_inserted"] += 1
            else:
                key = str(row.get("date_key") or "")[:10]
                if not key or len(key) != 10:
                    stats["next_offset"] = i + 1
                    continue
                prior = supabase.table("options_journal").select("date_key").eq("date_key",key).limit(1).execute()
                if prior.data:
                    stats["already_remote"] += 1
                else:
                    payload = {}
                    for c in ("date_key", "timestamp", "share_name", "option_contract", "strike_price",
                              "expiry_days", "underlying_spot", "lot_size", "entry_premium",
                              "current_option_price", "target_premium", "stop_loss_premium",
                              "total_capital", "ai_confidence", "implied_vol", "status", "pnl_pct", "last_audited"):
                        v = row.get(c)
                        if v is not None and not pd.isna(v):
                            payload[c] = v.item() if hasattr(v, "item") else v
                    supabase.table("options_journal").insert(payload).execute()
                    stats["options_inserted"] += 1
        except Exception as exc:
            stats["failed"] += 1
            if first_failed is None:
                first_failed = i
            stats["errors"].append(f"{kind}: {type(exc).__name__}: {str(exc)[:120]}")
            if stats["failed"] >= 3:
                break
        stats["next_offset"] = i + 1
    if first_failed is not None:
        stats["next_offset"] = first_failed  # allow retry; never silently skip a failed cloud record
    stats["more_to_check"] = stats["next_offset"] < len(records)
    return stats

def persist_daily_option_to_supabase(signal: dict):
    """Best-effort save for a newly logged option idea, without remote overwrite."""
    if not supabase or not isinstance(signal, dict):
        return
    date_key = str(signal.get("date_key") or "")[:10]
    if not date_key or st.session_state.get("last_option_cloud_verified") == date_key:
        return
    try:
        prior = supabase.table("options_journal").select("date_key").eq("date_key",date_key).limit(1).execute()
        if not prior.data:
            payload = {}
            for col in ("date_key", "timestamp", "share_name", "option_contract", "strike_price",
                        "expiry_days", "underlying_spot", "lot_size", "entry_premium",
                        "current_option_price", "target_premium", "stop_loss_premium", "total_capital",
                        "ai_confidence", "implied_vol", "status", "pnl_pct", "last_audited"):
                value = signal.get(col)
                if value is not None and not pd.isna(value):
                    payload[col] = value.item() if hasattr(value, "item") else value
            supabase.table("options_journal").insert(payload).execute()
        st.session_state["last_option_cloud_verified"] = date_key
    except Exception as exc:
        record_db_error("Save daily option history", exc)

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
    prior = min(0.90, max(0.10, (wins + 0.5 * CAL_PRIOR_TRADES) / (decided + CAL_PRIOR_TRADES)))   # your real win rate, gently pulled to 50% while results are few
    iso = None
    mode = f"Pulled toward your real win rate ({prior:.0%}, from {decided} clear results)"
    if len(xs) >= MIN_CALIBRATION_SAMPLES and len(set(ys)) == 2:
        try:
            from sklearn.isotonic import IsotonicRegression
            iso = IsotonicRegression(out_of_bounds="clip").fit(xs, ys)
            mode = f"Tuned on {len(xs)} real results"
        except Exception:
            iso = None

    ref = float(np.mean(xs)) if len(xs) >= 10 else 0.5      # how the AI usually scored its past picks

    def calibrate(p):
        # A share scoring like the AI's past picks gets your real win rate; only a clearly higher score earns more.
        base = prior + CONF_SHRINK * (float(p) - ref)
        if iso is not None:
            try:
                base = 0.5 * float(iso.predict([float(p)])[0]) + 0.5 * base
            except Exception:
                pass
        return min(0.95, max(0.05, base))

    return calibrate, {"mode": mode, "prior": prior, "decided": decided, "ref_score": round(ref, 3)}

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
            # Save a rollback copy of the previous champion before promotion.
            old = Path(LIVE_MODEL_PATH)
            if old.exists():
                archive = ROOT_DIR / "models" / "archive"
                archive.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.copy2(old, archive / f"previous_champion_{stamp}.pkl")
            temp = LIVE_MODEL_PATH + ".tmp"
            joblib.dump(new_bundle, temp)
            os.replace(temp, LIVE_MODEL_PATH)
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

def get_nse_option_quote(symbol: str, expiry_dt: datetime, strike: float, side: str = "CE"):
    """Live CE or PE bid / ask. Unavailable is None; estimates are never tradable."""
    if side not in ("CE", "PE"):
        return None
    data = _nse_get_json("https://www.nseindia.com/api/option-chain-equities?symbol=" +
                         urllib.parse.quote(clean_sym_name(symbol), safe=""))
    if not data or not _quote_age_is_valid(data):
        return None
    expiry_str = expiry_dt.strftime("%d-%b-%Y")
    for item in (data.get("records") or {}).get("data", []):
        try:
            if item.get("expiryDate") != expiry_str or float(item.get("strikePrice", 0)) != float(strike):
                continue
            q = item.get(side) or {}
            bid = _num(q.get("bidprice") or q.get("bidPrice"))
            ask = _num(q.get("askPrice") or q.get("askprice"))
            if 0 < bid <= ask:
                return {"bid":bid, "ask":ask, "oi":_num(q.get("openInterest")),
                        "iv":_num(q.get("impliedVolatility")),
                        "spread_pct":round(200*(ask-bid)/(ask+bid),2)}
        except (ValueError, TypeError):
            continue
    return None


def _quote_age_is_valid(chain: dict) -> bool:
    timestamp = (chain.get("records") or {}).get("timestamp")
    if not timestamp:
        return False
    try:
        ts = datetime.strptime(str(timestamp), "%d-%b-%Y %H:%M:%S")
        age = (ist_now().replace(tzinfo=None) - ts).total_seconds() / 60
        return -5 <= age <= 15
    except (ValueError, TypeError):
        return False


def parse_nse_lot_file(csv_content: str) -> dict:
    """Return {symbol:{'NOV-26':lots,...}} from the exchange's month-specific lot file.

    A stale / unparseable CSV intentionally returns {} (never fall back to old hardcoded sizes).
    """
    if not csv_content:
        return {}
    lines = csv_content.replace("\ufeff", "").splitlines()
    # Some NSE snapshots start with a title or explanatory rows before their header.
    start = next((i for i, line in enumerate(lines[:25])
                  if "," in line and ("SYMBOL" in line.upper() or "UNDERLYING" in line.upper())
                  and bool(re.search(r"[A-Z]{3}[- /]\d{2,4}",line.upper()))), None)
    if start is None:
        return {}
    df = _read_csv_text("\n".join(lines[start:]))
    if df.empty:
        return {}
    symbol_col = next((c for c in df.columns if c in ("SYMBOL", "UNDERLYING", "UNDERLYING SYMBOL")), None)
    if symbol_col is None:
        return {}
    month_columns = []
    for col in df.columns:
        match = re.search(r"([A-Z]{3})[- /](\d{2,4})", str(col).upper())
        if not match:
            continue
        mon, year = match.groups()
        if mon not in {"JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"}:
            continue
        month_columns.append((col, f"{mon}-{year[-2:]}"))
    result = {}
    for _, row in df.iterrows():
        sym = str(row[symbol_col]).strip().upper()
        if not _SYM_OK.fullmatch(sym):
            continue
        months = {}
        for col, month_key in month_columns:
            value = str(row[col]).replace(",", "").strip()
            if value.isdecimal() and 0 < int(value) <= 100_000:
                months[month_key] = int(value)
        if months:
            result[sym] = months
    return result


def _current_nse_lots() -> dict:
    """Current NSE month-specific F&O lot sizes (no guessed values)."""
    box = get_nse_http_box()
    now = time.time()
    cached = box.get("fo_lots")
    if cached and now-cached[0] < (21600 if cached[1] else 900):
        return cached[1]
    content = _http_text("https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv", timeout=10)
    lots = parse_nse_lot_file(content)
    box["fo_lots"] = (now, lots)
    return lots

def option_paper_feedback() -> dict:
    """Directional outcome evidence from old *live-quoted* paper options.

    Synthetic model-priced outcomes remain in the database but must not train buy ratings.
    The result is deliberately a small caution gate, NOT a calibrated forecast.
    """
    d = load_options_journal_df()
    if d.empty:
        return {}
    d = d[(d["status"].apply(classify_outcome) != "ACTIVE") &
          d["option_contract"].apply(is_live_option)].copy()
    if d.empty:
        return {}
    d["side"] = d["option_contract"].apply(lambda x: "PE" if re.search(r"\bPE\b",str(x)) else "CE")
    # Outcomes marked win/loss only: do not treat unclassified or still-open as training evidence.
    d = d[d["status"].apply(classify_outcome).isin(("WIN", "LOSS", "EXPIRED"))]
    d["pnl_pct"] = pd.to_numeric(d["pnl_pct"],errors="coerce")
    d = d[np.isfinite(d["pnl_pct"]) & (d["pnl_pct"] > -101) & (d["pnl_pct"] < 1000)]
    return {side:{"n":int(len(g)),"mean":float(g["pnl_pct"].mean()),
                  "profitable":float((g["pnl_pct"] > 0).mean())}
            for side,g in d.groupby("side")}


def scan_option_candidates(max_underlyings: int = 35) -> pd.DataFrame:
    """Rotate through the NSE F&O universe in batches; analyse BOTH CE and PE.

    Current real quote and confirmed actual NSE lot size are necessary for CONSIDER.
    Scan outputs are saved to option_watchlist for long-running audit history.
    """
    now = ist_now()
    lots = _current_nse_lots()
    if not lots:
        st.session_state["option_scan_note"] = "Cannot verify NSE lot sizes; option trade recommendations paused."
        return pd.DataFrame()
    all_eq, _ = get_all_nse_symbols()
    syms = sorted(set(lots).intersection(all_eq))
    if not syms:
        st.session_state["option_scan_note"] = "NSE F&O and equity universes could not be cross-checked."
        return pd.DataFrame()
    batch = max(1, min(max_underlyings, len(syms)))
    bucket_count = math.ceil(len(syms)/batch)
    bucket = (now.date().toordinal() + now.hour//2) % bucket_count
    selected = syms[bucket*batch:(bucket+1)*batch]
    prefetch_histories([t+".NS" for t in selected], "3mo", workers=6, ttl=SWEEP_TTL_SEC, time_budget=70)
    eq_used, opt_used = get_open_exposures()
    reviews = []
    feedback = option_paper_feedback()
    for name in selected:
        h = get_history_fast(name+".NS", "3mo")
        if h.empty or len(h)<35:
            continue
        c = h["Close"].astype(float)
        spot = float(c.iloc[-1])
        if not math.isfinite(spot) or spot <= 0:
            continue
        change5 = (spot/float(c.iloc[-6])-1)*100
        log_returns = np.log(c/c.shift(1)).dropna().tail(20)
        rv = float(log_returns.std()*np.sqrt(252)*100) if len(log_returns)>=15 else 0
        diff = c.diff().tail(14)
        up = float(diff.clip(lower=0).mean())
        down = float((-diff.clip(upper=0)).mean())
        rsi = 100-100/(1+up/max(1e-6, down))
        if abs(change5)<0.6:
            continue
        chain = _nse_get_json("https://www.nseindia.com/api/option-chain-equities?symbol=" + urllib.parse.quote(name,safe=""))
        if not isinstance(chain,dict):
            continue
        quote_fresh = is_entry_window_open() and _quote_age_is_valid(chain)
        try:
            dates = [(datetime.strptime(x,"%d-%b-%Y"),x) for x in (chain.get("records") or {}).get("expiryDates", [])]
        except (TypeError,ValueError):
            continue
        valid_dates = [(d,label) for d,label in dates if 8 <= (d.date()-now.date()).days <= 65]
        if not valid_dates:
            continue
        expiry, expiry_label = min(valid_dates,key=lambda x:x[0])
        days_left = (expiry.date()-now.date()).days
        series = [r for r in (chain.get("records") or {}).get("data",[]) if r.get("expiryDate")==expiry_label]
        blocked = evaluate_nse_filing_nlp(name).get("is_blocked",False)
        for side in ("CE", "PE"):
            contracts = [x for x in series if isinstance(x.get(side),dict) and _num(x.get("strikePrice"))>0]
            if not contracts:
                continue
            aim = spot * (1.01 if side=="CE" else 0.99)
            contract = min(contracts,key=lambda x:abs(float(x["strikePrice"])-aim))
            strike = float(contract["strikePrice"])
            data = contract[side]
            bid = _num(data.get("bidprice") or data.get("bidPrice"))
            ask = _num(data.get("askPrice") or data.get("askprice"))
            oi = _num(data.get("openInterest"))
            iv = _num(data.get("impliedVolatility"))
            expiry_month = expiry.strftime("%b-%y").upper()
            lot = int(lots[name].get(expiry_month, 0))
            if lot <= 0:
                continue  # never substitute a different expiry's contract size
            result = review_long_option(kind=side,spot=spot,strike=strike,bid=bid,ask=ask,
                lot_size=lot,days_left=days_left,open_interest=oi,iv_pct=iv,realized_vol_pct=rv,
                ret_5d_pct=change5,rsi=rsi,recent_filing_risk=blocked,
                equity_exposure=eq_used,option_exposure=opt_used,quote_fresh=quote_fresh)
            decision, why = result.decision, result.reason
            history = feedback.get(side, {})
            # Only real-quoted paper samples, enough observations, and clear persistent losses.
            # Pause new optimistic labels; retained data and rules are not erased.
            if decision == "CONSIDER" and history.get("n",0) >= 12 and history["mean"] < -10 and history["profitable"] < 0.45:
                decision = "WATCH"
                why = (f"Historical live-quote {side} paper ideas have averaged {history['mean']:+.1f}% "
                       f"over {history['n']} finished samples; require better validation")
            score = (100 if decision=="CONSIDER" else 50 if decision=="WATCH" else 0) + abs(change5)-result.spread_pct/3
            reviews.append({"Stock":name,"Type":side,"Direction":"Up" if side=="CE" else "Down",
                "Decision":decision,"Why":why,"Expiry":expiry.strftime('%d %b %Y'),
                "Days left":days_left,"Strike (₹)":strike,"Share price (₹)":round(spot,2),
                "Buy premium (₹)":ask,"Sell premium (₹)":bid,"Lot":lot,"Cost (₹)":result.cost_inr,
                "Maximum premium loss (₹)":result.max_loss_inr,"Breakeven at expiry (₹)":result.breakeven_spot,
                "Spread (%)":result.spread_pct,"Open interest":int(oi),"IV (%)":iv,
                "Historical volatility (%)":round(rv,1),"5-day share move (%)":round(change5,2),
                "Score":round(score,2),"Fresh quote":quote_fresh,
                "Checked at":now.strftime('%Y-%m-%d %H:%M:%S')})
    df = pd.DataFrame(reviews)
    if not df.empty:
        df = df.sort_values(["Score","Open interest"],ascending=[False,False]).reset_index(drop=True)
        with DB_LOCK:
            con=duckdb.connect(DB_PATH,read_only=False)
            try:
                for _,r in df.iterrows():
                    con.execute("""INSERT OR REPLACE INTO option_watchlist
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
                        now.date().isoformat(),r["Stock"],r["Type"],r["Expiry"],r["Strike (₹)"],
                        r["Share price (₹)"],r["Sell premium (₹)"],r["Buy premium (₹)"],
                        r["Lot"],r["Cost (₹)"],r["Decision"],r["Why"],r["Breakeven at expiry (₹)"],
                        r["Spread (%)"],r["IV (%)"],r["Historical volatility (%)"],
                        r["5-day share move (%)"],r["Checked at"]])
            finally:
                con.close()
    st.session_state["option_scan_note"] = f"Reviewed {len(selected)} of {len(syms)} exchange-listed F&O shares in this rotating batch."
    return df


def generate_daily_options_alpha() -> dict:
    """One highest-scored *paper* CE or PE per market day; no broker integration."""
    date_key = ist_now().strftime('%Y-%m-%d')
    with DB_LOCK:
        con=duckdb.connect(DB_PATH,read_only=True)
        try:
            old=con.execute("SELECT * FROM daily_options_journal WHERE date_key=?",[date_key]).df()
            if not old.empty:
                saved_option = old.iloc[0].to_dict()
                # Database fetch is completed before accessing the cloud below.
            else:
                saved_option = None
        finally:
            con.close()
    if saved_option is not None:
        persist_daily_option_to_supabase(saved_option)
        return saved_option
    if not get_history_integrity()["allow_new_paper_entries"]:
        st.session_state["opt_note"] = "New paper option ideas are paused until the missing share history is restored."
        return {}
    reviews = st.session_state.get("option_candidates")
    if not isinstance(reviews,pd.DataFrame) or reviews.empty or not is_entry_window_open():
        return {}
    choices = reviews[(reviews["Decision"]=="CONSIDER") & (reviews["Fresh quote"]==True)]
    if choices.empty:
        return {}
    best=choices.iloc[0]
    eq_used,opt_used=get_open_exposures()
    if not can_allocate_option(float(best["Cost (₹)"]),eq_used,opt_used):
        return {}
    now=ist_now(); expiry=datetime.strptime(best["Expiry"],'%d %b %Y')
    sig={"date_key":date_key,"timestamp":now.strftime('%Y-%m-%d %H:%M:%S'),
        "share_name":best["Stock"],"option_contract":f"{best['Stock']} {expiry.strftime('%d %b %Y').upper()} {best['Strike (₹)']:g} {best['Type']} (live quote)",
        "strike_price":float(best["Strike (₹)"]),"expiry_days":int(best["Days left"]),
        "underlying_spot":float(best["Share price (₹)"]),"lot_size":int(best["Lot"]),
        "entry_premium":float(best["Buy premium (₹)"]),"current_option_price":float(best["Sell premium (₹)"]),
        "target_premium":round(float(best["Buy premium (₹)"])*OPTION_TARGET_MULT,2),
        "stop_loss_premium":round(float(best["Buy premium (₹)"])*OPTION_STOP_MULT,2),
        "total_capital":float(best["Cost (₹)"]),"ai_confidence":round(min(100.0,float(best["Score"])),1),
        "implied_vol":float(best["IV (%)"]),"status":"ACTIVE",
        "pnl_pct":round(100*(float(best["Sell premium (₹)"])/float(best["Buy premium (₹)"])-1),2),
        "last_audited":now.strftime('%Y-%m-%d %H:%M:%S')}
    with DB_LOCK:
        con=duckdb.connect(DB_PATH,read_only=False)
        try:
            # avoid a concurrent duplicate paper entry; database enforces date_key uniqueness
            con.execute("""INSERT INTO daily_options_journal
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?) ON CONFLICT(date_key) DO NOTHING""",
                [sig[k] for k in ("date_key","timestamp","share_name","option_contract","strike_price",
                 "expiry_days","underlying_spot","lot_size","entry_premium","current_option_price",
                 "target_premium","stop_loss_premium","total_capital","ai_confidence","implied_vol",
                 "pnl_pct","last_audited")])
        finally:
            con.close()
    persist_daily_option_to_supabase(sig)
    return sig

def is_live_option(contract) -> bool:
    return "(live quote)" in str(contract)

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
    kind = "PE" if re.search(r"\bPE\b", str(opt["option_contract"])) else "CE"

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
            q = get_nse_option_quote(name, expiry_dt, strike, kind)
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
        live = max(0.0, (float(h["Close"].iloc[-1]) - strike) if kind == "CE"
                   else strike - float(h["Close"].iloc[-1]))  # expiry intrinsic, not a broker fill
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

NO_DATA_GRACE_DAYS = 3

def close_stale_trade(tr, now_str: str):
    """A share with NO prices at all while the market feed works (delisted or suspended): after its 7-day hold plus a few
    days' grace, close it at the last price we saw, clearly labelled, so it can't block a slot forever."""
    last = _num(tr.get("latest_price"), _num(tr.get("entry_price")))
    pnl = _num(tr.get("pnl_pct"))
    status = "⏱️ EXPIRED (NO PRICE DATA)"
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=False)
        try:
            con.execute("UPDATE trade_journal SET status = ?, exit_price = ?, exit_timestamp = ?, last_audited = ? WHERE trade_id = ?",
                        [status, last, now_str, now_str, tr["trade_id"]])
        finally:
            con.close()
    cloud_update("predictions", {"ticker": tr["ticker"], "predicted_date": tr["date_str"]},
                 {"status": status, "latest_price": last, "pnl_pct": pnl, "last_checked": now_str}, "Close Stale Trade")

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
    feed_ok = None
    for _, tr in (active_trades.iterrows() if not active_trades.empty else []):
        tkr = str(tr["ticker"]).strip()
        try:
            h = fetch_cached_history(normalize_ticker_for_yf(tkr), period="1mo", interval="1d")
            if h.empty:
                if feed_ok is None:
                    feed_ok = not fetch_cached_history("^NSEI", period="5d", interval="1d").empty
                entry_dt0 = _to_date(tr["date_str"])
                if feed_ok and entry_dt0 and (today_date - entry_dt0).days >= MAX_HOLD_CALENDAR_DAYS + NO_DATA_GRACE_DAYS:
                    close_stale_trade(tr, now_str)                     # share stopped trading (e.g. delisted): close at last known price
                else:
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
            return con.execute("SELECT ticker, entry_price, latest_price, target_price, stop_loss, capital_allocated, pnl_pct, status, features_json FROM trade_journal WHERE status = 'ACTIVE'").df()
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

def get_open_exposures() -> tuple:
    """Paper capital allocated to active stock and option ideas (combined INR cap)."""
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            eq = _num(con.execute("SELECT COALESCE(SUM(capital_allocated),0) FROM trade_journal WHERE status='ACTIVE'").fetchone()[0])
            opt = _num(con.execute("SELECT COALESCE(SUM(total_capital),0) FROM daily_options_journal WHERE status='ACTIVE'").fetchone()[0])
            return eq, opt
        finally:
            con.close()

def get_portfolio_capacity() -> dict:
    df = get_active_positions_df()
    risk = 0.0
    for _, r in (df.iterrows() if not df.empty else []):
        e, stop, cap = _num(r["entry_price"]), _num(r["stop_loss"]), _num(r["capital_allocated"])
        if e > stop > 0:
            risk += cap * (e - stop) / e
    eq, opt = get_open_exposures()
    remaining = remaining_capital(eq, opt)
    open_count = len(df)
    legacy_open_excess = max(0, open_count - MAX_OPEN_POSITIONS)
    fresh_slot_limit_reached = open_count >= MAX_OPEN_POSITIONS
    fresh_slots_left = max(0, MAX_OPEN_POSITIONS - min(open_count, MAX_OPEN_POSITIONS))
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            unpriced = int(con.execute("""SELECT COUNT(*) FROM trade_journal WHERE status='ACTIVE'
                AND (capital_allocated IS NULL OR capital_allocated<=0 OR entry_price IS NULL OR entry_price<=0)""").fetchone()[0])
            unpriced += int(con.execute("""SELECT COUNT(*) FROM daily_options_journal WHERE status='ACTIVE'
                AND (total_capital IS NULL OR total_capital<=0)""").fetchone()[0])
        finally:
            con.close()
    if unpriced:
        reason = f"{unpriced} open paper position(s) still have missing allocations; review History before adding more"
    elif fresh_slot_limit_reached:
        if legacy_open_excess > 0:
            reason = (f"{open_count} active share records are loaded, including {legacy_open_excess} legacy restored position(s). "
                      f"Fresh research ideas stay capped at {MAX_OPEN_POSITIONS} until some active positions are reconciled or closed.")
        else:
            reason = f"Fresh share ideas are capped at {MAX_OPEN_POSITIONS} active positions"
    elif risk >= MAX_OPEN_RISK_INR:
        reason = f"Open equity stop-risk limit {inr(MAX_OPEN_RISK_INR)} reached"
    elif remaining <= 0:
        reason = "Combined ₹10 lakh allocation already used"
    else:
        reason = ""
    return {
        "open": open_count,
        "risk_inr": round(risk,2),
        "equity_inr": round(eq,2),
        "option_inr":round(opt,2),
        "remaining_inr":round(remaining,2),
        "ok": not reason,
        "reason": reason,
        "fresh_slots_left": fresh_slots_left,
        "fresh_slot_limit_reached": fresh_slot_limit_reached,
        "legacy_open_excess": legacy_open_excess,
        "effective_limit": MAX_OPEN_POSITIONS,
    }

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
    if not get_history_integrity()["allow_new_paper_entries"]:
        return fail("Historical share records have not been reconciled. New paper entries are paused to avoid exceeding the true portfolio allocation.")
    if not bool(sig.get("Qualified", False)):
        return fail("Unqualified signal cannot be recorded as a research buy")
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
            entry, stop = float(sig['Price (₹)']), float(sig['Stop Loss (₹)'])
            equity_used = _num(con.execute("SELECT COALESCE(SUM(capital_allocated),0) FROM trade_journal WHERE status='ACTIVE'").fetchone()[0])
            options_used = _num(con.execute("SELECT COALESCE(SUM(total_capital),0) FROM daily_options_journal WHERE status='ACTIVE'").fetchone()[0])
            risk_used = _num(con.execute("""SELECT COALESCE(SUM(capital_allocated*(entry_price-stop_loss)/entry_price),0)
                FROM trade_journal WHERE status='ACTIVE' AND entry_price > stop_loss AND entry_price > 0""").fetchone()[0])
            allowed = allowable_equity_shares(entry, stop, equity_used, options_used, risk_used)
            if shares_num <= 0 or shares_num > allowed:
                return fail("Combined capital, single position or risk allowance prevents this paper trade")
            con.execute("""
                INSERT INTO trade_journal 
                (trade_id, timestamp, date_str, ticker, asset_type, entry_price, target_price,
                 stop_loss, shares, capital_allocated, status, latest_price, pnl_pct, exit_price,
                 exit_timestamp, last_audited, features_json)
                VALUES (?, ?, ?, ?, 'EQUITY', ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 0.0, NULL, ?, ?)
            """, [f"{ticker}_{today_str}", now_str, today_str, ticker, float(sig['Price (₹)']), float(sig['Target (₹)']), float(sig['Stop Loss (₹)']),
                  shares_num, round(shares_num * entry, 2), entry, init_net_pnl, now_str, f_json_str])
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

def expected_value_pct(p_win: float, entry: float, target: float, stop: float, friction_pct: float) -> float:
    """What a pick is worth on average, in %, after ALL costs: chance × net gain − (1 − chance) × net loss."""
    if entry <= 0:
        return 0.0
    gain = (target - entry) / entry * 100.0 - friction_pct
    loss = (entry - stop) / entry * 100.0 + friction_pct
    return round(p_win * gain - (1.0 - p_win) * loss, 2)

def breakeven_win_pct(entry: float, target: float, stop: float, friction_pct: float) -> float:
    """How often a trade like this must reach its goal (before its safety exit) just to break even after costs."""
    if entry <= 0:
        return 100.0
    gain = (target - entry) / entry * 100.0 - friction_pct
    loss = (entry - stop) / entry * 100.0 + friction_pct
    return round(loss / (gain + loss) * 100.0, 1) if gain > 0 and (gain + loss) > 0 else 100.0

def todays_learning_picks() -> int:
    t = get_todays_logged_equities()
    return int(sum(bool(_parse_features(f).get("learning_pick")) for f in t["features_json"])) if not t.empty and "features_json" in t.columns else 0

def is_learning_trade(f_json) -> bool:
    return bool(_parse_features(f_json).get("learning_pick"))

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
            live_price = round(live_price, 2)                      # the price the trade is recorded at
            cap_state = get_portfolio_capacity()
            shares = allowable_equity_shares(live_price, stop, cap_state["equity_inr"],
                        cap_state["option_inr"], cap_state["risk_inr"])
            capital = round(shares * live_price, 2)                    # so shares x price always equals money in
            slip = slippage_pct_per_side(avg_turnover)
            friction = compute_costs_pct(capital, slip)
            net_return = round(((target - live_price) / live_price) * 100.0 - friction, 2)
            sector = get_ticker_sector(clean_sym)
            rel_strength = round(info["ret20"] - nifty20, 2)

            ev_pct = expected_value_pct(final_score / 100.0, live_price, target, stop, friction)
            be_pct = breakeven_win_pct(live_price, target, stop, friction)
            safe = (is_above_trend and not is_exhausted and has_volume and not nlp["is_blocked"] and net_return >= MIN_NET_RETURN_PCT)
            qualified = safe and ev_pct > MIN_EV_PCT and shares > 0
            if shares <= 0:
                reason = "Insufficient available capital or stop-risk budget"
            elif nlp["is_blocked"]:
                reason = f"Bad news flagged: {nlp['nlp_tags']}"
            elif not is_above_trend:
                reason = "Not in an uptrend any more (fresh price)"
            elif is_exhausted:
                reason = "Already run up too far (overbought)"
            elif not has_volume:
                reason = f"Too little trading today (below {MIN_RVOL_THRESHOLD}× normal volume)"
            elif net_return < MIN_NET_RETURN_PCT:
                reason = f"Possible profit after costs is only {net_return:+.2f}%"
            elif ev_pct <= MIN_EV_PCT:
                reason = f"Not worth it after costs: needs a {be_pct:.0f}% chance of reaching the goal, the AI estimates {final_score:.0f}% (worth {ev_pct:+.2f}% on average)"
            else:
                reason = f"Passed every check (needs {be_pct:.0f}%, AI estimates {final_score:.0f}%)"

            snapshot = {
                "raw_features": raw_feature_map, "atr_pct": atr_pct, "raw_prob": round(raw_prob, 4), "base_ml_conf": base_conf,
                "learner_delta": learner["delta"], "macro_delta": macro_delta, "nlp_delta": nlp["delta"], "sector": sector,
                "friction_pct": friction, "avg_turnover_inr": round(avg_turnover, 0), "entry_bar": str(dates[-1]), "final_score": final_score,
                "rel_strength_20d": rel_strength, "ev_pct": ev_pct, "breakeven_pct": be_pct
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
                "Vs Nifty (20d)": rel_strength, "Expected value": ev_pct, "Needs to win (%)": be_pct, "Safe": safe, "Learning": False,
                "FeaturesDict": snapshot, "FullSymbol": f"{clean_sym}.NS"
            })
        except Exception:
            stats["errors"] += 1
    prog.empty()
    stats["deep"] = len(results)
    stats["seconds"] = round(time.time() - t_start, 1)

    if not results:
        st.session_state["scan_stats"] = stats
        return pd.DataFrame(), False
    df_sorted = pd.DataFrame(results).sort_values(by=["Qualified", "Expected value", "Adjusted Score"], ascending=[False, False, False]).reset_index(drop=True)
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
    decided_n = int((cal_info or {}).get("decided", 0))
    stats["learning_mode"] = decided_n < MIN_CALIBRATION_SAMPLES
    stats["decided"] = decided_n
    stats["learning_logged"] = 0
    loggable_confident = not qualified_only.empty and bool(qualified_only["Loggable"].any())
    if stats["learning_mode"] and not paused and not loggable_confident:
        room = LEARNING_PICKS_PER_DAY - todays_learning_picks()
        pool = df_sorted[(df_sorted["Safe"] == True) & (df_sorted["Qualified"] == False) & (df_sorted["Loggable"] == True)]
        pool = pool.sort_values(by=["Adjusted Score", "Expected value"], ascending=[False, False])
        for idx, sig in pool.iterrows():
            if room <= 0 or todays_learning_picks() >= LEARNING_PICKS_PER_DAY or len(get_todays_logged_equities()) >= MAX_DAILY_EQUITY_TRADES:
                break                                       # re-checked right before buying, in case another open tab just bought one
            d = sig.to_dict()
            d["FeaturesDict"] = dict(d["FeaturesDict"], learning_pick=True)
            if log_equity_signal_safely(d, enforce_sector_cap=True):
                df_sorted.at[idx, "Learning"] = True
                df_sorted.at[idx, "Rejection Reason"] = "🧪 Bought as today's learning pick (paper money, so the AI keeps learning)"
                stats["learning_logged"] += 1
                stats["logged"] += 1
                room -= 1
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
    if "NO PRICE" in str(status).upper():
        return "⏱️ Closed — the share stopped trading (last known price)"
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

def mini_trade_html(ticker, sector, entry, target, stop, live, pnl_pct, locked, shares, learning: bool = False) -> str:
    cls = "pos" if pnl_pct >= 0 else "neg"
    return _collapse(f"""
    <div class="mini">
      <div class="row"><span class="mtk">{esc(ticker)}{' 🛡️' if locked else ''}{' 🧪' if learning else ''}</span><span class="mp {cls}">{pnl_pct:+.2f}%</span></div>
      {price_bar_html(entry, target, stop, live, compact=True)}
      <div class="sub2">{esc(sector)} · now ₹{live:,.2f} · {int(shares)} shares{' · learning pick' if learning else ''}</div>
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

# No charts are created or displayed: all results use words and tables.

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

def _safe_num_series(s):
    return pd.to_numeric(s, errors="coerce") if s is not None else pd.Series(dtype=float)


def summarise_learning_progress() -> dict:
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            all_shares = con.execute("SELECT status, features_json, date_str FROM trade_journal").df()
        except Exception:
            all_shares = pd.DataFrame()
        finally:
            con.close()
    if all_shares.empty:
        return {"completed":0, "win_rate":None, "latest_retrain":"Not eligible yet", "last_promotion":"None recorded"}
    completed_mask = all_shares["status"].apply(classify_outcome).isin(["WIN","LOSS","BE","EXPIRED"])
    completed = int(completed_mask.sum())
    wins = int((all_shares["status"].apply(classify_outcome) == "WIN").sum())
    decisive = int(all_shares["status"].apply(classify_outcome).isin(["WIN","LOSS"]).sum())
    win_rate = (wins / decisive * 100.0) if decisive else None
    last_state_raw = get_meta("retrain_state", "{}")
    try:
        train_state = json.loads(last_state_raw or "{}")
    except (ValueError, TypeError):
        train_state = {}
    return {
        "completed": completed,
        "win_rate": win_rate,
        "latest_retrain": train_state.get("last_attempt_date", "Not eligible yet"),
        "last_promotion": train_state.get("last_promoted", "None recorded"),
    }


def select_top_share_ideas(df: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["Decision"] = work.apply(lambda r: "CONSIDER" if r.get("Qualified", False)
        else "AVOID" if "bad news" in str(r.get("Rejection Reason", "")).lower() or "risk" in str(r.get("Rejection Reason", "")).lower()
        else "WATCH", axis=1)
    pref = {"CONSIDER": 0, "WATCH": 1, "AVOID": 2}
    work["_rank_decision"] = work["Decision"].map(pref).fillna(9)
    work["_rank_score"] = pd.to_numeric(work.get("Adjusted Score"), errors="coerce").fillna(-9999)
    work["_rank_ev"] = pd.to_numeric(work.get("Expected value"), errors="coerce").fillna(-9999)
    work = work.sort_values(["_rank_decision", "_rank_score", "_rank_ev"], ascending=[True, False, False])
    return work.head(limit)


def select_top_option_ideas(df: pd.DataFrame, limit_each: int = 2) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    work = df.copy()
    pref = {"CONSIDER": 0, "WATCH": 1, "AVOID": 2}
    work["_rank_decision"] = work["Decision"].map(pref).fillna(9)
    work["_rank_oi"] = pd.to_numeric(work.get("Open interest"), errors="coerce").fillna(0)
    work["_rank_spread"] = pd.to_numeric(work.get("Spread (%)"), errors="coerce").fillna(9999)
    work["_rank_days"] = pd.to_numeric(work.get("Days left"), errors="coerce").fillna(9999)
    out = []
    for side in ["CE", "PE"]:
        side_df = work[work.get("Type").astype(str) == side].sort_values(
            ["_rank_decision", "_rank_spread", "_rank_oi", "_rank_days"],
            ascending=[True, True, False, True]
        )
        if not side_df.empty:
            out.append(side_df.head(limit_each))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def top_recent_closed(limit: int = 5) -> pd.DataFrame:
    with DB_LOCK:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            df = con.execute("""SELECT COALESCE(exit_timestamp, last_audited, date_str) AS closed_at,
                                      ticker, status, pnl_pct
                               FROM trade_journal
                               WHERE status != 'ACTIVE'
                               ORDER BY COALESCE(exit_timestamp, last_audited, date_str) DESC NULLS LAST""").df()
        except Exception:
            df = pd.DataFrame()
        finally:
            con.close()
    return df.head(limit) if not df.empty else df

# ==============================================================================
# 14. BRIGHT, INTERACTIVE, GRAPH-FREE DASHBOARD + HISTORY RECOVERY
# ==============================================================================
from dashboard_theme import THEME_CSS, hero as ui_hero, kpis as ui_kpis, item_card as ui_card, intro as ui_intro
from history_recovery import merge_history_database, snapshot_database, count_history
import tempfile
import logging

st.markdown(f"<style>{THEME_CSS}</style>", unsafe_allow_html=True)
state = get_market_state()
market_open = is_nse_market_open()
now = ist_now()

with st.sidebar:
    st.markdown("### ✨ Your research desk")
    st.caption("Easy to read • No charts • Decisions explained")
    st.markdown("**Paper-trading capital:** ₹10,00,000")
    auto_refresh = st.toggle("Auto-refresh while open", value=True, key="main_autorefresh")
    st.markdown("---")
    with st.expander("⚙️ Trading assumptions"):
        st.caption("Stock ideas: normally ₹1 lakh, up to ₹2 lakh. Option lot: ₹50,000, overall option cap ₹2 lakh.")
        st.number_input("Brokerage per order (₹)", min_value=0.0, max_value=100.0,
                        value=DEFAULT_BROKERAGE_PER_ORDER_INR, key="brokerage_inr")
        st.number_input("DP charge per sale (₹)", min_value=0.0, max_value=100.0,
                        value=DEFAULT_DP_CHARGE_INR, key="dp_charge_inr")
    with st.expander("🩺 Data and system status"):
        jc = journal_counts()
        st.caption(f"DuckDB schema v{DB_SCHEMA_STATUS.get('version', '?')}")
        st.write(f"📚 Shares saved locally: **{jc['total']:,}**")
        st.caption(f"Expected historical share baseline: at least {HISTORY_EXPECTED_MIN_SHARES:,} records")
        st.write(f"📚 Options saved locally: **{len(load_options_journal_df()):,}**")
        st.write(f"☁️ Supabase: **{'configured' if supabase else 'not configured'}**")
        hi = st.session_state.get("hydrate_info", {})
        if hi:
            st.caption(f"Last cloud check: {hi.get('remote',0)} remote share rows, {hi.get('remote_options',0)} option rows; {hi.get('restored',0)} share rows restored")
        if st.session_state.get("db_error"):
            st.error(st.session_state["db_error"])
        st.caption("Cloud-hosted local files can disappear after a restart. History is only durable if backed up externally.")
    if st.button("↪️ Sign out", use_container_width=True):
        st.session_state["password_correct"] = False
        st.rerun()

if auto_refresh and st_autorefresh:
    st_autorefresh(interval=15*60*1000, key="bright_autorefresh")

st.markdown(ui_hero(now.strftime('%d %b %Y · %H:%M'), market_open), unsafe_allow_html=True)
cap = get_portfolio_capacity()
counts = journal_counts()
integrity = get_history_integrity()
opt_all = load_options_journal_df()
opt_open = (opt_all[opt_all["status"].apply(classify_outcome) == "ACTIVE"]
            if not opt_all.empty else pd.DataFrame())

st.markdown(ui_kpis([
    ("Total research budget", "₹10,00,000", "Combined shares and options", "🏦", "lav"),
    ("Available capacity", inr(cap["remaining_inr"]) if integrity["verified_count"] else "Unverified",
     "New paper entries paused until history is restored" if not integrity["verified_count"] else "Available for new paper ideas", "✨", "teal"),
    ("Shares allocated", inr(cap["equity_inr"]), f"{cap['open']} active stock ideas", "📈", ""),
    ("Options allocated", inr(cap["option_inr"]), f"{len(opt_open)} open CE / PE ideas", "🎯", ""),
]), unsafe_allow_html=True)
if not integrity["verified_count"]:
    st.error(f"🗂️ **Previous paper history is incomplete: {integrity['local']} of at least "
             f"{integrity['minimum']} expected share records are available locally.** "
             "The displayed share allocation may be incomplete. "
             "New automatic paper entries are paused; scans and existing records remain accessible.")
    st.warning(integrity["reason"] + " Open **History & learning → Recover previous data** or retry the cloud restore below. "
               "Do not reset the database or model files.")
    if supabase:
        if st.button("☁️ Retry restoring earlier shares from Supabase", key="overview_retry_cloud_restore", type="primary"):
            with st.spinner("Checking your original Supabase paper-history tables..."):
                hydrate_duckdb_from_supabase()
            st.rerun()
    else:
        st.info("Set SUPABASE_URL and SUPABASE_KEY in **Streamlit Cloud → App settings → Secrets** "
                "for your **original** Supabase project, or upload a backed-up .duckdb file in History & learning.")
if not cap["ok"]:
    st.info("ℹ️ " + cap["reason"])
if DB_SCHEMA_STATUS.get("unpriced_active_shares", 0) or DB_SCHEMA_STATUS.get("unpriced_active_options", 0):
    st.warning("Some old active positions have missing allocations. Review History before trusting the capital available.")
if not ANNOUNCEMENTS_AVAILABLE:
    st.caption("ℹ️ Corporate filings module is unavailable; news-based checks may be incomplete.")

page_home, page_shares, page_options, page_history = st.tabs([
    "✨ Overview", "🏢 Stocks", "🎯 Calls & puts", "📚 History & learning"
])

with page_home:
    st.markdown(ui_intro("Your portfolio at a glance", "Paper portfolio • not actual broker holdings"), unsafe_allow_html=True)
    learning = summarise_learning_progress()
    share_snapshot = st.session_state.get("simple_share_results", pd.DataFrame())
    option_snapshot = st.session_state.get("option_candidates", pd.DataFrame())
    top_shares = select_top_share_ideas(share_snapshot, 3)
    top_options = select_top_option_ideas(option_snapshot, 2)
    if not market_open:
        st.caption("🌙 NSE closed: the ideas below are **previous-scan research**, not fresh executable quotes. "
                   "Revalidate prices and news after the market opens.")
    if learning["completed"] == 0:
        st.caption("🧠 No local completed share outcomes are available to verify these displayed scores. "
                   "The model's estimated percentages should not be treated as proven win probabilities.")

    p1, p2 = st.columns([1.2, 1])
    with p1:
        legacy_note = (f"<p><b>{cap['legacy_open_excess']} legacy open record(s)</b> are included in the active count. "
                       "These came from restored history and are preserved; the fresh-idea rule still caps new share ideas at 10.</p>") if cap.get("legacy_open_excess", 0) else ""
        st.markdown("<div class='panel'><h3>🌱 Where things stand</h3>"
                    f"<p><b>{cap['open']} share records</b> and <b>{len(opt_open)} options</b> are currently being followed. "
                    f"Fresh share idea slots left: <b>{cap.get('fresh_slots_left', 0)}</b> out of <b>{cap.get('effective_limit', MAX_OPEN_POSITIONS)}</b>.</p>"
                    f"<p>Estimated stop-risk on shares: <b>{inr(cap['risk_inr'])}</b> out of {inr(MAX_OPEN_RISK_INR)} planned. Stop exits are not guaranteed.</p>"
                    + legacy_note +
                    "</div>", unsafe_allow_html=True)
    with p2:
        wr = "Not enough finished results yet" if learning["win_rate"] is None else f"{learning['win_rate']:.1f}% win rate on decided outcomes"
        st.markdown("<div class='panel'><h3>🧠 AI learning snapshot</h3>"
                    f"<p><b>{learning['completed']:,}</b> completed share outcomes are available for evaluation.</p>"
                    f"<p>{wr}</p>"
                    f"<p>Last retrain attempt: <b>{learning['latest_retrain']}</b><br>"
                    f"Last promoted model: <b>{learning['last_promotion']}</b></p></div>",
                    unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(ui_intro("Top stock ideas right now", "Best 3 from the last scan"), unsafe_allow_html=True)
        if isinstance(top_shares, pd.DataFrame) and not top_shares.empty:
            for _, r in top_shares.iterrows():
                reason = str(r.get("Rejection Reason") or "Met model and research checks")
                st.markdown(ui_card(str(r["Ticker"]), str(r["Decision"]), str(r["Sector"]), [
                    ("Current share price", inr(r["Price (₹)"],2)),
                    ("Expected value", f"{_num(r.get('Expected value')):+.2f}%"),
                    ("Model score", f"{_num(r.get('Adjusted Score')):.1f}")], reason), unsafe_allow_html=True)
        else:
            st.info("Run the stock scan to populate the top share ideas.")
    with c2:
        st.markdown(ui_intro("Best call & put setups", "Top CE and PE reviews"), unsafe_allow_html=True)
        if isinstance(top_options, pd.DataFrame) and not top_options.empty:
            for _, r in top_options.iterrows():
                name = f"{r['Stock']} · {r['Type']} {'↗' if r['Type']=='CE' else '↘'}"
                st.markdown(ui_card(name, str(r["Decision"]), f"{r['Expiry']} · strike {inr(r['Strike (₹)'],2)}", [
                    ("One-lot premium", inr(r["Cost (₹)"])),
                    ("Break-even", inr(r["Breakeven at expiry (₹)"],2)),
                    ("Max risk", inr(r["Maximum premium loss (₹)"]))], str(r.get("Why") or "Contract reviewed")),
                    unsafe_allow_html=True)
        else:
            if not market_open:
                st.info("No currently validated CE / PE contracts. NSE is closed, so real bid/ask quotes cannot be refreshed. Review again during market hours.")
            else:
                st.info("No validated CE / PE contract from the latest scan. Open Calls & puts to retry; no trade is required.")

    d1, d2 = st.columns([1.2, 1])
    with d1:
        st.markdown("<div class='panel'><h3>💡 Simple decision guide</h3>"
                    "<p>🟢 <b>CONSIDER</b> — research filters passed, not a buy instruction.</p>"
                    "<p>🟡 <b>WATCH</b> — data is uncertain or the setup is incomplete.</p>"
                    "<p>🔴 <b>AVOID</b> — a major check failed or risk is too high.</p>"
                    "<p>CE = call if bullish. PE = put if bearish. A long option can lose its full premium.</p></div>",
                    unsafe_allow_html=True)
    with d2:
        closed_preview = top_recent_closed(4)
        if closed_preview.empty:
            st.markdown("<div class='panel'><h3>📚 Recent finished ideas</h3><p>No finished records yet in this local database.</p></div>", unsafe_allow_html=True)
        else:
            lines = []
            for _, rr in closed_preview.iterrows():
                lines.append(f"<p><b>{esc(rr['ticker'])}</b> · {esc(rr['status'])} · {(_num(rr['pnl_pct'])):+.2f}%</p>")
            st.markdown("<div class='panel'><h3>📚 Recent finished ideas</h3>" + "".join(lines) + "</div>", unsafe_allow_html=True)

    action1, action2 = st.columns(2)
    with action1:
        if st.button("🔄 Review open positions now", use_container_width=True, key="review_positions_now"):
            with st.spinner("Refreshing open share and option records..."):
                audit_and_reconcile_all_trades()
            st.success("Open paper positions reviewed.")
            st.rerun()
    with action2:
        if st.button("📥 Refresh both idea lists", use_container_width=True, key="refresh_both_lists"):
            with st.spinner("Refreshing stock and option research lists..."):
                audit_and_reconcile_all_trades()
                st.session_state["simple_share_results"], _ = run_predictions()
                st.session_state["simple_share_epoch"] = time.time()
                st.session_state["option_candidates"] = scan_option_candidates()
                st.session_state["options_epoch"] = time.time()
                st.session_state["last_update_ist"] = ist_now().strftime('%d %b %H:%M IST')
            st.success("Stock and option research lists refreshed.")
            st.rerun()

    with st.expander(f"📋 See {cap['open']} active share positions"):
        open_eq = get_active_positions_df()
        if open_eq.empty:
            st.caption("No active share ideas in this local database.")
        else:
            columns = ["ticker", "entry_price", "latest_price", "target_price", "stop_loss", "capital_allocated", "pnl_pct", "status"]
            st.dataframe(open_eq[columns].rename(columns={"ticker":"Share","entry_price":"Entry ₹",
                "latest_price":"Latest ₹","target_price":"Target ₹","stop_loss":"Stop ₹",
                "capital_allocated":"Allocated ₹","pnl_pct":"Paper P/L %","status":"Status"}),
                hide_index=True, use_container_width=True)
    with st.expander(f"📜 See {len(opt_open)} active call / put positions"):
        if opt_open.empty:
            st.caption("No active option ideas in this local database.")
        else:
            st.dataframe(opt_open[["option_contract","entry_premium","current_option_price","total_capital","pnl_pct","status"]]
                .rename(columns={"option_contract":"Contract","entry_premium":"Entry premium ₹",
                "current_option_price":"Latest premium ₹","total_capital":"Premium committed ₹",
                "pnl_pct":"Paper P/L %","status":"Status"}),hide_index=True,use_container_width=True)
    if counts["total"] == 0 and len(opt_all) == 0:
        st.warning("📭 No previous trade history is currently available on this deployment. "
                   "Open **History & learning → Recover previous data** to reconnect Supabase or upload your old DuckDB backup.")
    elif not supabase:
        st.info("📚 Your existing local history is visible, but it isn't backed up to Supabase here. "
                "Download an external backup from **History & learning**.")

with page_shares:
    st.markdown(ui_intro("Stock ideas", "Whole eligible NSE EQ universe • rotating scans"), unsafe_allow_html=True)
    left, right = st.columns([2, 1])
    with left:
        st.caption("No penny-stock-only filter. All prices are eligible for research if basic liquidity and data checks pass.")
    with right:
        rescan_shares = st.button("🔄 Analyse stocks", type="primary", key="refresh_shares", use_container_width=True)
    if rescan_shares or "simple_share_results" not in st.session_state or (auto_refresh and time.time()-st.session_state.get("simple_share_epoch",0)>900):
        with st.spinner("Reading market prices and running the trained stock model..."):
            audit_and_reconcile_all_trades()
            share_ideas, cleared = run_predictions()
            st.session_state["simple_share_results"] = share_ideas
            st.session_state["simple_share_epoch"] = time.time()
            st.session_state["last_update_ist"] = ist_now().strftime('%d %b %H:%M IST')
    shares = st.session_state.get("simple_share_results", pd.DataFrame())
    scan = st.session_state.get("scan_stats", {})
    if scan.get("error"):
        st.error(scan["error"])
    if scan.get("source"):
        st.caption(f"🛰️ NSE list: {scan.get('listed',0):,} shares · {scan['source']}")
    if scan.get("partial"):
        st.warning("Only part of the market was scanned this time; results are incomplete.")
    if isinstance(shares, pd.DataFrame) and not shares.empty:
        shares = shares.copy()
        st.success(f"Showing the latest scan across the eligible NSE share universe. Fresh share idea slots left: {cap.get('fresh_slots_left', 0)} / {cap.get('effective_limit', MAX_OPEN_POSITIONS)}")
        shares["Decision"] = shares.apply(lambda r: "CONSIDER" if r["Qualified"]
            else "AVOID" if "Bad news" in str(r["Rejection Reason"]) or "risk" in str(r["Rejection Reason"]).lower()
            else "WATCH", axis=1)
        a,b = st.columns([1.3,2])
        with a:
            choice = st.segmented_control("Signal", ["CONSIDER","WATCH","AVOID","ALL"],default="CONSIDER",key="share_decision_filter")
        with b:
            needle = st.text_input("Find a stock", placeholder="Search by symbol or sector...", key="stock_search")
        view = shares if choice == "ALL" else shares[shares["Decision"] == choice]
        if needle.strip():
            view = view[view["Ticker"].astype(str).str.contains(re.escape(needle.strip()),case=False,na=False) |
                        view["Sector"].astype(str).str.contains(re.escape(needle.strip()),case=False,na=False)]
        st.caption(f"{len(view)} matching ideas · last scan {st.session_state.get('last_update_ist','not yet')} ")
        if view.empty:
            st.info("No matching candidates. A blank result is safer than an invented recommendation.")
        else:
            for _, r in view.head(6).iterrows():
                st.markdown(ui_card(str(r["Ticker"]), str(r["Decision"]), str(r["Sector"]), [
                    ("Current share price", inr(r["Price (₹)"],2)),
                    ("Profit target (estimate)", inr(r["Target (₹)"],2)),
                    ("Risk stop (not guaranteed)", inr(r["Stop Loss (₹)"],2))],
                    str(r["Rejection Reason"] if str(r["Rejection Reason"]).strip() else "Met model and research checks")),
                    unsafe_allow_html=True)
            if len(view) > 6:
                st.caption(f"Showing 6 of {len(view)} — use Search or the table below for more.")
        with st.expander("🧾 See all evaluated stocks and estimated allocations"):
            display_cols = ["Ticker","Sector","Decision","Price (₹)","Expected value","Adjusted Score",
                            "Target (₹)","Stop Loss (₹)","Total Cost (₹)","Rejection Reason"]
            st.dataframe(view[display_cols].rename(columns={"Ticker":"Stock","Expected value":"Modelled EV %",
                "Adjusted Score":"Model score","Rejection Reason":"Reason"}), hide_index=True,use_container_width=True)
    else:
        st.info("No verified stock candidates are available right now. Check price feeds and whether the original model file is present.")
    st.caption("Model score, price target and expected value are uncertain estimates, not guaranteed returns.")

with page_options:
    st.markdown(ui_intro("Calls and puts", "CE: bullish • PE: bearish"), unsafe_allow_html=True)
    st.caption("Only long stock options are analysed. A CONSIDER label requires an available NSE contract quote and current lot size.")
    rescan_options = st.button("🔄 Analyse options", type="primary", key="refresh_options")
    if rescan_options or "option_candidates" not in st.session_state or (auto_refresh and time.time()-st.session_state.get("options_epoch",0)>900):
        with st.spinner("Reviewing NSE contract quotes and current expiry details..."):
            st.session_state["option_candidates"] = scan_option_candidates()
            st.session_state["options_epoch"] = time.time()
            st.session_state["last_update_ist"] = ist_now().strftime('%d %b %H:%M IST')
    options = st.session_state.get("option_candidates", pd.DataFrame())
    st.caption(st.session_state.get("option_scan_note", "Option scan not run yet."))
    st.caption("A CE benefits from bullish movement; a PE benefits from bearish movement. The app prefers contracts with live quotes, tighter spreads and usable open interest.")
    if isinstance(options, pd.DataFrame) and not options.empty:
        a,b = st.columns([1.4,1])
        with a:
            opt_filter = st.segmented_control("Status", ["CONSIDER","WATCH","AVOID","ALL"],default="CONSIDER",key="option_decision_filter")
        with b:
            kind_filter = st.segmented_control("Direction", ["BOTH","CE","PE"],default="BOTH",key="option_kind_filter")
        filtered = options if opt_filter == "ALL" else options[options["Decision"] == opt_filter]
        filtered = filtered if kind_filter == "BOTH" else filtered[filtered["Type"] == kind_filter]
        st.caption(f"{len(filtered)} matching live contract reviews")
        if filtered.empty:
            st.info("Nothing qualifies under these filters. You don't have to take a trade.")
        else:
            for _, r in filtered.head(5).iterrows():
                name = f"{r['Stock']} · {r['Type']} {'↗' if r['Type']=='CE' else '↘'}"
                st.markdown(ui_card(name, str(r["Decision"]), f"{r['Expiry']} · strike {inr(r['Strike (₹)'],2)}", [
                    ("One-lot premium", inr(r["Cost (₹)"])),
                    ("Break-even at expiry", inr(r["Breakeven at expiry (₹)"],2)),
                    ("Max premium at risk", inr(r["Maximum premium loss (₹)"]))],
                    str(r["Why"])), unsafe_allow_html=True)
                with st.expander(f"🔍 {r['Stock']} {r['Type']} · contract details"):
                    st.caption(f"Bid ₹{r['Sell premium (₹)']:,.2f} · Ask ₹{r['Buy premium (₹)']:,.2f} · "
                        f"Lot {int(r['Lot'])} · Expiry in {r['Days left']} days · Spread {r['Spread (%)']:.1f}% · "
                        f"OI {r['Open interest']:,} · IV {r['IV (%)']:.1f}%")
            if len(filtered)>5:
                st.caption(f"Showing 5 of {len(filtered)}; see the full table below.")
        with st.expander("📋 See all call/put reviews"):
            cols = ["Stock","Type","Decision","Expiry","Strike (₹)","Buy premium (₹)",
                    "Lot","Cost (₹)","Breakeven at expiry (₹)","Maximum premium loss (₹)","Why"]
            st.dataframe(filtered[cols], hide_index=True,use_container_width=True)
    else:
        st.info("No live NSE quotes validated during this check. The app will not pretend theoretical option prices are buyable quotes.")
    idea = generate_daily_options_alpha()
    if idea:
        st.success(f"📝 Paper idea logged: {idea['option_contract']} · Premium budget {inr(idea['total_capital'])}")
    st.caption("⚠️ Long options can lose their full premium. Stock-option exercise can involve physical settlement; do not rely on a stop order to prevent this.")

with page_history:
    st.markdown(ui_intro("History, learning and recovery", "All previous records • no graphs"), unsafe_allow_html=True)
    if not integrity["verified_count"]:
        st.warning(f"History check: {integrity['local']} of {integrity['minimum']} previously expected share records are present. "
                   "Restore the original history before relying on paper-profit totals or retraining.")
    try:
        local_counts = count_history(DB_PATH)
    except Exception as e:
        st.error(f"Could not count the historical records: {type(e).__name__}")
        local_counts = {"trade_journal":0,"daily_options_journal":0,"option_watchlist":0}
    st.markdown(ui_kpis([
        ("Saved stock records", f"{local_counts['trade_journal']:,}", "Includes open and older records", "📚", "lav"),
        ("Saved option ideas", f"{local_counts['daily_options_journal']:,}", "CE and PE paper history", "📜", ""),
        ("Option checks stored", f"{local_counts['option_watchlist']:,}", "Includes rejected candidates", "🔎", ""),
        ("Cloud history", "Configured" if supabase else "Not linked", "Cloud credentials status", "☁️", "teal" if supabase else ""),
    ]), unsafe_allow_html=True)
    hx1, hx2 = st.columns(2)
    with hx1:
        if st.button("🔄 Reconcile all open trades", key="history_reconcile_open", use_container_width=True):
            with st.spinner("Reviewing all active paper positions..."):
                audit_and_reconcile_all_trades()
            st.success("Open positions reconciled.")
            st.rerun()
    with hx2:
        st.caption("Use the recovery tools below if earlier records are missing after deployment or restart.")
    with st.expander("🔁 Recover previous data", expanded=(sum(local_counts.values())==0)):
        st.markdown("**Two safe ways to bring back older records**")
        st.caption("(1) Retry Supabase if this deployment has your existing SUPABASE_URL and SUPABASE_KEY secrets. "
                   "(2) Upload an earlier market_data.duckdb or backed-up .duckdb file from your computer. "
                   "Neither method deliberately deletes current records.")
        if supabase:
            if st.button("☁️ Restore history from Supabase", key="retry_cloud_history", use_container_width=True):
                with st.spinner("Checking remote historical paper records..."):
                    hydrate_duckdb_from_supabase()
                st.rerun()
            hi = st.session_state.get("hydrate_info", {})
            if hi:
                st.caption(f"Last cloud restore: {hi.get('remote',0):,} stock rows and "
                           f"{hi.get('remote_options',0):,} option rows found; "
                           f"{hi.get('restored',0):,} records restored; "
                           f"{hi.get('bad_rows',0):,} skipped.")
            with st.expander("📤 Save missing local history to your Supabase", expanded=False):
                st.caption("Uploads records that are absent from legacy predictions/options_journal tables. Existing remote records are never overwritten. "
                           "The option research watchlist is not synced by this feature. Verify cloud totals after syncing.")
                if st.button("Sync up to 200 historical rows", key="sync_to_supabase"):
                    with st.spinner("Checking existing remote rows and uploading only missing records..."):
                        stats = sync_local_history_to_supabase(200, st.session_state.get("history_sync_offset",0))
                    st.session_state["history_sync_offset"] = stats["next_offset"] if stats["more_to_check"] else 0
                    if stats["failed"]:
                        st.error(f"Cloud sync incomplete: {stats['failed']} failed. " + "; ".join(stats["errors"][:2]))
                    else:
                        st.success(f"New cloud rows: {stats['shares_inserted']} shares, {stats['options_inserted']} options. "
                                   f"Already present: {stats['already_remote']}.")
                    if stats["more_to_check"]:
                        st.info(f"Processed {stats['next_offset']:,} of {stats['total_rows']:,} rows. Click sync again to continue.")
            if hi and hi.get("remote", 0) + hi.get("remote_options", 0) == 0 and sum(local_counts.values()) == 0:
                st.warning("The configured cloud tables returned no historical records. Verify that these secrets point to your **original** Supabase project.")
        else:
            st.warning("Supabase is not connected here. Your Cloud deployment cannot automatically restore older records from it. "
                       "Set SUPABASE_URL and SUPABASE_KEY in Streamlit Secrets if you already used Supabase.")
        upload = st.file_uploader("Or choose your old DuckDB file", type=["duckdb"],
                                  help="Select the saved market_data.duckdb from your old project or backups folder.")
        if upload is not None:
            st.caption(f"Selected {upload.name} · {upload.size / 1024 / 1024:.1f} MB")
            if upload.size > 50 * 1024 * 1024:
                st.error("For safety this upload is limited to 50 MB. Use Supabase or a managed import for a larger file.")
            elif st.button("📥 Merge my old history", key="merge_old_history", type="primary"):
                with st.spinner("Backing up current records and merging new history IDs..."):
                    try:
                        with tempfile.TemporaryDirectory(prefix="nse_history_restore_") as td:
                            old_path = Path(td) / "uploaded_old_history.duckdb"
                            old_path.write_bytes(upload.getvalue())
                            with DB_LOCK:
                                recovery = merge_history_database(old_path, DB_PATH, ROOT_DIR / "backups")
                        st.session_state["history_import_result"] = recovery
                        st.session_state["history_sync_offset"] = 0
                        st.rerun()
                    except Exception as e:
                        logging.exception("Failed to merge uploaded historical database")
                        st.error(f"Import stopped safely: {type(e).__name__}: {str(e)[:250]}")
        if st.session_state.get("history_import_result"):
            recovered = st.session_state["history_import_result"]
            st.success("Merge completed. New stock rows: "
                       f"{recovered['added'].get('trade_journal',0)}, option rows: "
                       f"{recovered['added'].get('daily_options_journal',0)}, "
                       f"research reviews: {recovered['added'].get('option_watchlist',0)}. Existing rows were kept.")
            st.warning("This import is local to the running server. Download a backup now. To survive Cloud restarts, "
                       "you need a persistent remote database and a verified sync strategy.")
        if st.button("💾 Prepare downloadable DuckDB backup", key="prepare_history_backup"):
            with DB_LOCK:
                snap = snapshot_database(DB_PATH, ROOT_DIR / "backups", "downloadable_history")
                st.session_state["history_backup_bytes"] = snap.read_bytes()
                st.session_state["history_backup_date"] = ist_now().strftime('%Y%m%d_%H%M')
        if st.session_state.get("history_backup_bytes"):
            st.download_button("⬇️ Download my full paper-history database",st.session_state["history_backup_bytes"],
                file_name=f"NSE_history_{st.session_state['history_backup_date']}.duckdb",
                mime="application/octet-stream",use_container_width=True)
    # The old screen showed only the last 100 closed 'known' outcomes, hiding OPEN/UNKNOWN.
    # This ledger displays ALL local history including legacy unknown statuses.
    with DB_LOCK:
        dbcon = duckdb.connect(DB_PATH, read_only=True)
        try:
            all_shares = dbcon.execute("SELECT trade_id,date_str,ticker,status,entry_price,pnl_pct,capital_allocated,features_json FROM trade_journal ORDER BY date_str DESC NULLS LAST").df()
            all_options = dbcon.execute("SELECT date_key,option_contract,status,entry_premium,total_capital,pnl_pct FROM daily_options_journal ORDER BY date_key DESC NULLS LAST").df()
            all_reviews = dbcon.execute("SELECT date_key,symbol,side,decision,reason,checked_at FROM option_watchlist ORDER BY checked_at DESC NULLS LAST").df()
        finally:
            dbcon.close()
    st.markdown(ui_intro("Every recorded stock idea", "Open, finished and legacy records"), unsafe_allow_html=True)
    if all_shares.empty:
        st.info("No stock records in the current database yet. Use Recover previous data above.")
    else:
        f1, f2 = st.columns([1, 2])
        with f1:
            states = sorted(all_shares["status"].fillna("UNKNOWN").astype(str).unique())
            state_filter = st.selectbox("Filter by outcome", ["All outcomes"] + states, key="history_state_filter")
        with f2:
            history_search = st.text_input("Find an earlier stock", placeholder="e.g. SBIN or RELIANCE", key="history_search")
        history_view = all_shares
        if state_filter != "All outcomes":
            history_view = history_view[history_view["status"].fillna("UNKNOWN").astype(str)==state_filter]
        if history_search.strip():
            history_view = history_view[history_view["ticker"].astype(str).str.contains(re.escape(history_search.strip()),case=False,na=False)]
        st.caption(f"Showing {len(history_view):,} of {len(all_shares):,} stored records. UNKNOWN records are still preserved, but cannot be treated as confirmed wins/losses.")
        st.dataframe(history_view[["date_str","ticker","status","entry_price","capital_allocated","pnl_pct"]]
            .rename(columns={"date_str":"Date","ticker":"Share","status":"Result","entry_price":"Entry ₹",
                             "capital_allocated":"Capital ₹","pnl_pct":"Paper P/L %"}),
            hide_index=True,use_container_width=True)
        st.download_button("⬇️ Export visible share history (CSV)",history_view.to_csv(index=False).encode(),
            file_name="share_history.csv",mime="text/csv")
        labelled = all_shares[all_shares["status"].apply(classify_outcome).isin(["WIN","LOSS","BE","EXPIRED"])].copy()
        st.caption(f"🧠 {len(labelled):,} recorded completed outcomes are available for evaluation; "
                   "training further requires validated features, labels and model-quality checks.")
    st.markdown(ui_intro("Previous option ideas", "All dates, not only recent entries"),unsafe_allow_html=True)
    if all_options.empty:
        st.caption("No option paper history stored on this deployment.")
    else:
        st.dataframe(all_options.rename(columns={"date_key":"Date","option_contract":"Contract",
            "status":"Result","entry_premium":"Entry premium ₹","total_capital":"Capital ₹","pnl_pct":"Paper P/L %"}),
            hide_index=True,use_container_width=True)
    with st.expander(f"🔎 See {len(all_reviews):,} saved CE/PE research reviews"):
        if all_reviews.empty:
            st.caption("No individual contract reviews have been stored yet.")
        else:
            st.dataframe(all_reviews,hide_index=True,use_container_width=True)
    with st.expander("🧠 How the model learns from past records"):
        st.caption(f"A challenger is considered after at least {MIN_SETTLED_TO_RETRAIN} validated completed share cases, "
                   f"then after {RETRAIN_STEP_INTERVAL} additional cases. It replaces the current model only if it beats "
                   "the champion on later-period validation. Saved training history isn't deleted by this interface.")
        last_state_raw = get_meta("retrain_state", "{}")
        try:
            train_state = json.loads(last_state_raw or "{}")
        except (ValueError,TypeError):
            train_state = {}
        st.write("Last retrain attempt:", train_state.get("last_attempt_date", "Not eligible yet"))
        st.write("Last model promotion:", train_state.get("last_promoted", "None recorded"))
        st.caption("The original trained-model file and any live model file must be kept alongside the project. "
                   "Restoring the DuckDB file alone does not restore missing model .pkl files.")
    st.caption("Streamlit auto-refresh works while a browser session is open. Fully unattended ten-year research would need "
               "a separate scheduled service, persistent storage, backups, monitoring and periodic maintenance.")

st.caption("Research and paper trading only. No broker orders are sent; all price forecasts and option ratings are uncertain.")
