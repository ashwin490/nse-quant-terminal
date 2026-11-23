"""Selection of recent, distinct stock outcomes for the read-only AI-learning feed.

The feed is an interpretation of completed paper journal rows, NOT a model
retraining event. Do not infer a win or loss from the stock's latest open trade.
"""
from __future__ import annotations

import pandas as pd
from learning_insights import _outcome

COMPLETED = frozenset(('WIN', 'LOSS', 'BE', 'EXPIRED'))


def latest_completed_by_stock(history: pd.DataFrame) -> list[dict]:
    """Return newest completed event for EACH unique ticker, newest first.

    Prefer saved exit timestamps. When absent, fall back to the recorded
    date, not the current date. Does not change the original journal.
    """
    if history is None or history.empty or not {'ticker', 'status'}.issubset(history.columns):
        return []

    records = history.copy()
    records['_feed_symbol'] = records['ticker'].fillna('').astype(str).str.strip().str.upper()
    records = records[records['_feed_symbol'].ne('') & records['_feed_symbol'].ne('NAN')].copy()
    records = records[records['status'].apply(_outcome).isin(COMPLETED)].copy()
    if records.empty:
        return []

    # Last audit is NOT a trustworthy close date for legacy entries; only
    # choose it if no trade/exit timestamp was ever recorded.
    ordering = pd.Series(pd.NaT, index=records.index, dtype='datetime64[ns, UTC]')
    for name in ('exit_timestamp', 'date_str', 'timestamp', 'last_audited'):
        if name in records.columns:
            parsed = pd.to_datetime(records[name], errors='coerce', utc=True)
            ordering = ordering.fillna(parsed)
    records['_feed_date'] = ordering
    records['_feed_row_order'] = range(len(records))
    records = records.sort_values(['_feed_date', '_feed_row_order'],
                                  ascending=[False, False], na_position='last', kind='mergesort')
    # Each card represents a different stock. The row itself is the actual
    # most recently completed outcome, not an invented composite result.
    records = records.drop_duplicates('_feed_symbol', keep='first')
    return records.drop(columns=['_feed_symbol', '_feed_date', '_feed_row_order']).to_dict('records')
