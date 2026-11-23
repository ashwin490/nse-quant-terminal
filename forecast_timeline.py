"""Honest, non-ML empirical *research* timelines for long-equity targets.

A timeline is a historical first-touch study, not a future prediction. Completed
bars only, and stop-first if a daily OHLC candle crosses both barriers.
Independent-looking samples are limited by using a stride; overlapping paths
can still remain when stride < horizon. Nothing here executes a trade.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TimelineEstimate:
    label: str
    target_hits: int
    stop_hits: int
    neither: int
    trials: int
    horizon_sessions: int
    target_rate_pct: float | None
    typical_sessions: int | None
    lower_sessions: int | None
    upper_sessions: int | None
    credible: bool
    note: str


def estimate_target_timeline(bars: pd.DataFrame, entry: float, target: float, stop: float,
                             max_sessions: int = 5, *, min_trials: int = 15) -> TimelineEstimate:
    """Estimate target-before-stop frequency over previous *completed* daily bars.

    A previous day's close is an entry level. The following daily highs/lows
    are measured as percentage moves vs that close. This allows matching a
    present trade's relative target and stop distance without price-level bias.
    Only historical data are used, with no future prices at prediction time.
    A matched candle counts as a stop (conservative unknown intraday order).
    """
    horizon = max(1, min(int(max_sessions), 10))
    empty = TimelineEstimate('Insufficient history', 0, 0, 0, 0, horizon, None, None, None, None,
                             False, 'Not enough completed price bars to estimate timing.')
    try:
        entry, target, stop = float(entry), float(target), float(stop)
    except (TypeError, ValueError):
        return empty
    if not all(map(math.isfinite, [entry, target, stop])) or not (0 < stop < entry < target):
        return empty
    if bars is None or not {'Close', 'High', 'Low'}.issubset(bars.columns):
        return empty
    vals = bars[['Close', 'High', 'Low']].apply(pd.to_numeric, errors='coerce').dropna()
    vals = vals[(vals > 0).all(axis=1)]
    # Session-local clock is deliberately not referenced; caller must exclude
    # today's incomplete candle. See app.get_timeline_for_stock.
    if len(vals) < horizon + 18:
        return empty
    close = vals['Close'].to_numpy(dtype=float)
    high = vals['High'].to_numpy(dtype=float)
    low = vals['Low'].to_numpy(dtype=float)
    up = target / entry
    down = stop / entry
    # Stride 3 reduces overlap and keeps a reasonable sample with 6mo data.
    stride = max(3, horizon // 2)
    target_days, stops, neither = [], 0, 0
    # Do not touch any future bar beyond the known historical input.
    for i in range(0, len(close) - horizon, stride):
        base = close[i]
        outcome = 'neither'
        for d in range(1, horizon + 1):
            # If both target and stop are within one bar, assume stop first.
            if low[i + d] / base <= down:
                stops += 1
                outcome = 'stop'
                break
            if high[i + d] / base >= up:
                target_days.append(d)
                outcome = 'target'
                break
        if outcome == 'neither':
            neither += 1
    trials = len(target_days) + stops + neither
    if not trials:
        return empty
    rate = 100 * len(target_days) / trials
    typical = int(round(float(np.median(target_days)))) if len(target_days) >= 6 else None
    lower = int(round(float(np.percentile(target_days, 25)))) if len(target_days) >= 6 else None
    upper = int(round(float(np.percentile(target_days, 75)))) if len(target_days) >= 6 else None
    credible = trials >= min_trials and len(target_days) >= 6
    if credible:
        label = f'{lower}–{upper} trading sessions' if lower != upper else f'{typical} trading sessions'
    else:
        label = 'Timing not established'
    note = (f'Historical study: target first in {len(target_days)}/{trials} sampled windows '
            f'({rate:.0f}%), stop first in {stops}/{trials}, neither in {neither}/{trials} '
            f'within {horizon} sessions. Overlapping windows, changing markets and unverified fill costs '
            'make this unsuitable as a calibrated future probability.')
    if trials < min_trials:
        note += ' Sample is too small for a timing estimate.'
    elif len(target_days) < 6:
        note += ' Too few target hits for a useful timing range.'
    return TimelineEstimate(label, len(target_days), stops, neither, trials, horizon,
                            round(rate, 1), typical, lower, upper, credible, note)


def completed_bars_only(bars: pd.DataFrame, ist_today, market_open: bool) -> pd.DataFrame:
    """Remove today's incomplete NSE candle for an open-market backstudy."""
    if bars is None or bars.empty or not market_open:
        return bars
    idx = pd.to_datetime(bars.index, errors='coerce')
    if len(idx) and pd.notna(idx[-1]) and idx[-1].date() == ist_today:
        return bars.iloc[:-1]
    return bars
