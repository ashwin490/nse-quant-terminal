"""Small, testable read-only dashboard summaries; never change any journal data.

The 'recent closed' preview and the AI results count deliberately share the
same source snapshot. This prevents one tile claiming settled records exist
while a second, separate query silently reports none.
"""
from __future__ import annotations

import pandas as pd


def outcome_of(status) -> str:
    value = str(status or '').upper()
    if 'WIN' in value:
        return 'WIN'
    if 'BREAK-EVEN' in value:
        return 'BE'
    if 'LOSS' in value:
        return 'LOSS'
    if 'EXPIRED' in value:
        return 'EXPIRED'
    if 'ACTIVE' in value:
        return 'ACTIVE'
    return 'OTHER'


def summarize_share_journal(frame: pd.DataFrame | None, recent_limit: int = 4) -> dict:
    """Count and preview outcomes using identical statuses and one snapshot.

    Results tagged EXPIRED are historical outcomes, but must not automatically
    be treated as validated ML labels (e.g. NO PRICE DATA is not a true fill).
    """
    empty = pd.DataFrame(columns=['ticker', 'status', 'pnl_pct', 'closed_at'])
    baseline = {
        'total': 0, 'open': 0, 'settled': 0, 'decided': 0,
        'wins': 0, 'win_rate': None, 'recent': empty,
    }
    if frame is None or frame.empty or 'status' not in frame:
        return baseline

    rows = frame.copy()
    rows['_outcome'] = rows['status'].apply(outcome_of)
    settled_mask = rows['_outcome'].isin(('WIN', 'LOSS', 'BE', 'EXPIRED'))
    decided = rows['_outcome'].isin(('WIN', 'LOSS'))
    wins = int((rows['_outcome'] == 'WIN').sum())
    decided_n = int(decided.sum())

    recent = rows.loc[settled_mask].copy()
    # A few older cloud datasets do not have an exit_timestamp. Falling back
    # to last_audited and date_str is for sorting, not reconstructing fills.
    closed_at = pd.Series(pd.NaT, index=recent.index, dtype='datetime64[ns]')
    for source in ('exit_timestamp', 'last_audited', 'date_str'):
        if source in recent.columns:
            parsed = pd.to_datetime(recent[source], errors='coerce', format='mixed', utc=True).dt.tz_localize(None)
            closed_at = closed_at.fillna(parsed)
    recent['closed_at'] = closed_at
    if 'ticker' not in recent:
        recent['ticker'] = 'UNKNOWN'
    if 'pnl_pct' not in recent:
        recent['pnl_pct'] = None
    recent = recent.sort_values('closed_at', ascending=False, na_position='last', kind='stable')
    recent = recent[['ticker', 'status', 'pnl_pct', 'closed_at']].head(max(0, recent_limit))

    return {
        'total': int(len(rows)),
        'open': int((rows['_outcome'] == 'ACTIVE').sum()),
        'settled': int(settled_mask.sum()),
        'decided': decided_n,
        'wins': wins,
        'win_rate': (wins * 100.0 / decided_n) if decided_n else None,
        'recent': recent,
    }
