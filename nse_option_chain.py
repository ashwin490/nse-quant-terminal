"""NSE equity-option chain normalization and conservative quote selection.

Pure helpers: never place orders, create synthetic quotes, or assume freshness when
an exchange timestamp is absent.  Works with the expiry-scoped v3 flow and
legacy chain shapes; the caller owns network access and paper-trade decisions.
"""
from __future__ import annotations

from datetime import date, datetime
import math


def positive_number(value) -> float:
    try:
        out = float(str(value).replace(',', '').strip())
        return out if math.isfinite(out) and out > 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _as_records(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    return payload.get('records') if isinstance(payload.get('records'), dict) else {}


def parse_expiry_date(value) -> date | None:
    """Parse only unambiguous exchange expiry formats; never guess a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or '').strip()
    for fmt in ('%d-%b-%Y', '%d %b %Y', '%Y-%m-%d', '%d-%b-%y'):
        try:
            return datetime.strptime(raw.title(), fmt).date()
        except ValueError:
            continue
    return None


def expiry_labels(payload: dict) -> list[str]:
    """Normalize expiry labels from the exchange only; never infer calendar dates."""
    if not isinstance(payload, dict):
        return []
    raw = _as_records(payload).get('expiryDates') or payload.get('expiryDates') or []
    if not isinstance(raw, list):
        return []
    good = []
    for value in raw:
        dt = parse_expiry_date(value)
        if dt is None:
            continue
        label = dt.strftime('%d-%b-%Y')
        if label not in good:
            good.append(label)
    return good

def supported_expiries(payload: dict, month_lots: dict, today: date,
                       min_days: int = 8, max_days: int = 65) -> list[tuple[str, int, int]]:
    """(expiry label, days remaining, verified lot). Never borrow a lot from another month."""
    good = []
    for label in expiry_labels(payload):
        dt = datetime.strptime(label, '%d-%b-%Y')
        days = (dt.date() - today).days
        lot = int(positive_number(month_lots.get(dt.strftime('%b-%y').upper())))
        if min_days <= days <= max_days and lot > 0:
            good.append((label, days, lot))
    return sorted(good, key=lambda x: x[1])


def matching_rows(payload: dict, expiry: str) -> list[dict]:
    """Select only exact-expiry records; search both NSE records and filtered arrays.

    Some responses contain unrelated dates under records.data but the requested
    date under filtered.data. Never mix those dates or infer missing dates.
    """
    wanted = parse_expiry_date(expiry)
    if not isinstance(payload, dict) or wanted is None:
        return []
    records = _as_records(payload)
    filt = payload.get('filtered') if isinstance(payload.get('filtered'), dict) else {}
    for rows in (records.get('data'), filt.get('data')):
        if not isinstance(rows, list):
            continue
        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # A row-level expiry, when present, wins over a conflicting leg.
            # Conflicting leg dates are bad data and must never be mixed.
            explicit_row_date = parse_expiry_date(row.get('expiryDate'))
            if explicit_row_date is not None:
                dates = [row.get('expiryDate')]
            else:
                dates = []
                for side in ('CE', 'PE'):
                    leg = row.get(side)
                    if isinstance(leg, dict):
                        dates.append(leg.get('expiryDate'))
            # Explicit dates on the row or legs are required; a v3 URL alone
            # is insufficient evidence to accept an otherwise undated option.
            if any(parse_expiry_date(value) == wanted for value in dates):
                matches.append(row)
        if matches:
            return matches
    return []


def option_payload_shape(payload: dict, expiry: str) -> tuple[str, str]:
    """Safe shape summary for an HTTP-200 payload, never raw market quotes."""
    if not isinstance(payload, dict):
        return 'not-json-object', 'JSON object missing'
    records = _as_records(payload)
    filt = payload.get('filtered') if isinstance(payload.get('filtered'), dict) else {}
    all_lists = [x for x in (records.get('data'), filt.get('data')) if isinstance(x, list)]
    if matching_rows(payload, expiry):
        return 'matching', 'matching expiry rows found'
    if not all_lists:
        keys = ', '.join(sorted(str(k)[:28] for k in payload)[:5])
        return 'unknown-shape', 'no records.data/filtered.data lists; top-level keys: ' + (keys or 'none')
    rows = [r for group in all_lists for r in group if isinstance(r, dict)]
    if not rows:
        return 'empty', 'records.data/filtered.data contain no option rows'
    seen = set()
    for row in rows:
        for key in ('expiryDate',):
            dt = parse_expiry_date(row.get(key))
            if dt is not None:
                seen.add(dt.strftime('%d-%b-%Y'))
        for side in ('CE', 'PE'):
            leg = row.get(side)
            if isinstance(leg, dict):
                dt = parse_expiry_date(leg.get('expiryDate'))
                if dt is not None:
                    seen.add(dt.strftime('%d-%b-%Y'))
    if seen:
        return 'other-expiry', 'available expiry labels: ' + ', '.join(sorted(seen)[:3])
    return 'undated', 'strike rows supplied without explicit expiry dates'

def exchange_timestamp(payload: dict):
    """Timestamp from exchange payload only; never use client retrieval time."""
    if not isinstance(payload, dict):
        return None
    records = _as_records(payload)
    filt = payload.get('filtered') if isinstance(payload.get('filtered'), dict) else {}
    for raw in (records.get('timestamp'), filt.get('timestamp'), payload.get('timestamp')):
        if not isinstance(raw, str):
            continue
        for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%b-%Y %H:%M', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(raw.strip().title(), fmt)
            except ValueError:
                continue
    return None


def quote_is_fresh(payload: dict, now_ist: datetime, max_age_minutes=15) -> bool:
    exchange_dt = exchange_timestamp(payload)
    if exchange_dt is None:
        return False
    age = (now_ist.replace(tzinfo=None) - exchange_dt).total_seconds() / 60
    return -5 <= age <= max_age_minutes


def underlying_value(payload: dict) -> float:
    records = _as_records(payload)
    spot = positive_number(records.get('underlyingValue'))
    if spot:
        return spot
    filt = payload.get('filtered') if isinstance(payload.get('filtered'), dict) else {}
    spot = positive_number(filt.get('underlyingValue'))
    if spot:
        return spot
    for row in records.get('data') or []:
        if not isinstance(row, dict):
            continue
        for side in ('CE', 'PE'):
            leg = row.get(side)
            if isinstance(leg, dict):
                spot = positive_number(leg.get('underlyingValue'))
                if spot:
                    return spot
    return 0.0


def bid_ask(leg: dict) -> tuple[float, float]:
    """Never substitute last traded premium for a missing executable bid/ask."""
    if not isinstance(leg, dict):
        return 0.0, 0.0
    bid = positive_number(leg.get('bidprice') if 'bidprice' in leg else leg.get('bidPrice'))
    ask = positive_number(leg.get('askPrice') if 'askPrice' in leg else leg.get('askprice'))
    if not (0 < bid <= ask):
        return 0.0, 0.0
    for field in ('bidQty', 'askQty'):
        if field in leg and positive_number(leg[field]) <= 0:
            return 0.0, 0.0
    return bid, ask


def nearby_strikes(rows: list[dict], side: str, spot: float,
                   max_strikes: int = 7, max_distance_pct: float = 8.0) -> list[dict]:
    """Evaluate ATM/near-OTM strikes, rather than discarding a side for one bad quote.

    Max seven candidate strikes in one side, from a bounded 8% range.
    Strike ranks are deterministic, and the caller still applies all guardrails.
    """
    if side not in ('CE', 'PE') or not (spot > 0 and math.isfinite(spot)):
        return []
    desired = spot * (1.01 if side == 'CE' else 0.99)
    eligible = []
    unique = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(side), dict):
            continue
        strike = positive_number(row.get('strikePrice'))
        if not strike or abs(strike - spot) / spot * 100 > max_distance_pct or strike in unique:
            continue
        unique.add(strike)
        eligible.append((abs(strike - desired), abs(strike - spot), strike, row))
    eligible.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[3] for x in eligible[:max_strikes]]


def exact_contract(rows: list[dict], side: str, strike: float) -> dict | None:
    if side not in ('CE', 'PE') or positive_number(strike) <= 0:
        return None
    for row in rows:
        if (isinstance(row, dict) and isinstance(row.get(side), dict) and
                abs(positive_number(row.get('strikePrice')) - float(strike)) < 0.005):
            return row[side]
    return None
