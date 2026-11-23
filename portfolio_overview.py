"""Pure, non-trading summary of the last recorded open paper portfolio.

Never infers executed fills, and never treats absent price fields as zero P/L.
"""
from __future__ import annotations
import math
import re
import pandas as pd


def _number(value):
    try:
        num = float(value)
        return num if math.isfinite(num) else None
    except (TypeError, ValueError):
        return None


def _is_open(value) -> bool:
    return re.search(r'(?<![A-Z])ACTIVE(?![A-Z])', str(value or '').upper()) is not None


def _option_side(contract) -> str:
    match = re.search(r'\b(CE|PE)\b', str(contract or '').upper())
    return match.group(1) if match else 'UNKNOWN'


def _open_rows(df):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or 'status' not in df.columns:
        return []
    return df.loc[df['status'].apply(_is_open)].to_dict('records')


def portfolio_open_snapshot(equities: pd.DataFrame, options: pd.DataFrame) -> dict:
    """Counts all active records, values only records with complete positive marks.

    pnl_pct in the journals is already the app's estimated net paper percentage;
    do not subtract costs again or count closed trades as open P/L. No live
    execution / full market-data freshness guarantee is implied.
    """
    eq_open, opt_open = _open_rows(equities), _open_rows(options)
    count_ce = count_pe = count_unknown = 0
    valid = 0
    open_pnl = 0.0
    invalid = []
    for row in eq_open:
        amount = _number(row.get('capital_allocated'))
        pct = _number(row.get('pnl_pct'))
        entry = _number(row.get('entry_price'))
        last = _number(row.get('latest_price'))
        if amount is None or amount <= 0 or pct is None or entry is None or entry <= 0 or last is None or last <= 0:
            invalid.append(str(row.get('ticker') or 'unknown share'))
            continue
        open_pnl += amount * pct / 100.0
        valid += 1
    for row in opt_open:
        side = _option_side(row.get('option_contract'))
        if side == 'CE':
            count_ce += 1
        elif side == 'PE':
            count_pe += 1
        else:
            count_unknown += 1
        amount = _number(row.get('total_capital'))
        pct = _number(row.get('pnl_pct'))
        entry = _number(row.get('entry_premium'))
        last = _number(row.get('current_option_price'))
        if amount is None or amount <= 0 or pct is None or entry is None or entry <= 0 or last is None or last <= 0:
            invalid.append(str(row.get('option_contract') or 'unknown option'))
            continue
        open_pnl += amount * pct / 100.0
        valid += 1
    return {
        'shares': len(eq_open), 'calls': count_ce, 'puts': count_pe,
        'other_options': count_unknown, 'options': len(opt_open),
        'open_total': len(eq_open) + len(opt_open),
        'paper_pnl_inr': round(open_pnl, 2),
        'valued_count': valid, 'missing_count': len(invalid),
        'missing_identifiers': invalid,
        'complete': len(invalid) == 0,
    }
