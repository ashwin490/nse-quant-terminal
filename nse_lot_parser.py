"""Read NSE fo_mktlots.csv without mistaking UNDERLYING company name for SYMBOL.

The NSE CSV commonly places UNDERLYING before SYMBOL. Matching a company-name
column to the exchange EQ ticker list can leave only 1 apparent F&O instrument.
No stale/default lot sizes are ever invented by this parser.
"""
from __future__ import annotations

import csv
import io
import re

MONTHS = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
VALID_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")
MONTH_HEADING = re.compile(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[- /](\d{2}|20\d{2})\b", re.I)


def parse_nse_lot_file(csv_content: str) -> dict[str, dict[str, int]]:
    """Return symbol -> month (e.g. NOV-26) -> positive exchange lot size.

    May return {} on any incompatible or invalid file. Callers must fail closed.
    """
    if not isinstance(csv_content, str) or not csv_content.strip():
        return {}
    rows = list(csv.reader(io.StringIO(csv_content.lstrip('\ufeff'))))
    header_idx = None
    normalized_header = []
    for idx, row in enumerate(rows[:30]):
        normalized = [str(c).strip().upper() for c in row]
        if (any(c in normalized for c in ('SYMBOL', 'UNDERLYING SYMBOL')) and
                any(MONTH_HEADING.search(c) for c in normalized)):
            header_idx = idx
            normalized_header = normalized
            break
    if header_idx is None:
        return {}
    # Critical: NSE often lists "UNDERLYING,SYMBOL,NOV-26" in that order.
    # NEVER pick UNDERLYING while a precise SYMBOL column exists.
    sym_idx = next((normalized_header.index(c) for c in ('SYMBOL', 'UNDERLYING SYMBOL')
                    if c in normalized_header), None)
    if sym_idx is None:
        return {}
    months = {}
    for idx, col in enumerate(normalized_header):
        m = MONTH_HEADING.search(col)
        if not m:
            continue
        mon, yr = m.group(1).upper(), m.group(2)
        if mon in MONTHS:
            months[idx] = f'{mon}-{yr[-2:]}'
    if not months:
        return {}
    result = {}
    for row in rows[header_idx+1:]:
        if sym_idx >= len(row):
            continue
        symbol = str(row[sym_idx]).strip().upper()
        if not VALID_SYMBOL.fullmatch(symbol):
            continue
        available = {}
        for idx, mon in months.items():
            if idx >= len(row):
                continue
            raw = str(row[idx]).strip().replace(',', '')
            if raw.isdecimal():
                lot = int(raw)
                if 0 < lot <= 100_000:
                    available[mon] = lot
        if available:
            result[symbol] = available
    return result
