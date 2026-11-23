"""Conservative 1-minute OHLC reconciliation for NSE equity *paper* positions.

This module deliberately never calls a broker. A verified first touch can be
recorded even when the price has returned inside the thresholds before the next
15-minute dashboard refresh. Missing time bars or a same-minute double touch
produce an explicit uncertainty indicator.

The source must be a timestamp-indexed, IST-local, *1-minute* OHLC DataFrame.
Daily candles must never be supplied to this function.
"""
from dataclasses import dataclass
from datetime import datetime
import math
import pandas as pd


@dataclass(frozen=True)
class IntradayCheck:
    outcome: str | None  # 'WIN', 'STOP', or None
    fill: float | None
    touched_at: str | None
    new_stop: float
    checked: int
    covered: bool
    note: str
    ambiguous: bool = False


def scan_intraday_minutes(bars: pd.DataFrame, *, entry: float, target: float,
                          stop: float, from_time: datetime, as_of: datetime,
                          be_floor: float = 0.0, be_trigger: float = float('inf'),
                          max_gap_minutes: int = 3) -> IntradayCheck:
    """Review completed minute candles in time order, never claiming unseen hits.

    A candle whose opening minute predates `from_time` is excluded to avoid
    counting highs/lows that occurred before paper entry. When a feed gap occurs,
    later touches cannot establish which barrier was crossed first; return
    uncertain rather than manufacturing a win or loss.
    """
    pending = IntradayCheck(None, None, None, float(stop), 0, False,
                            'Intraday minute-price coverage unavailable; threshold history unverified.')
    if bars is None or bars.empty or not {'Open', 'High', 'Low', 'Close'}.issubset(bars.columns):
        return pending
    try:
        prices = [float(entry), float(target), float(stop)]
        # A paper stop may have been ratcheted ABOVE entry to protect gains.
        # Reject nonsensical thresholds, but do not confuse a protected stop
        # with missing minute-bar coverage (as the old `stop < entry` check did).
        if not all(map(math.isfinite, prices)) or not (0 < entry < target and 0 < stop < target):
            return pending
        start, now = pd.Timestamp(from_time), pd.Timestamp(as_of)
        if start.tzinfo is not None:
            start = start.tz_convert('Asia/Kolkata').tz_localize(None)
        if now.tzinfo is not None:
            now = now.tz_convert('Asia/Kolkata').tz_localize(None)
        if now <= start:
            return pending
        data = bars[['Open', 'High', 'Low', 'Close']].copy()
        ix = pd.DatetimeIndex(pd.to_datetime(data.index))
        if ix.tz is not None:
            ix = ix.tz_convert('Asia/Kolkata').tz_localize(None)
        data.index = ix
        # Only fully completed candles; last live minute may still change.
        first_complete = start.ceil('min')
        latest_start = now - pd.Timedelta(minutes=1)
        data = data[(data.index >= first_complete) & (data.index <= latest_start)]
        data = data.sort_index()
        data = data[~data.index.duplicated(keep='last')]
        if data.empty:
            return pending
        nums = data.apply(pd.to_numeric, errors='coerce')
        nums = nums.dropna()
        if nums.empty:
            return pending
        current_stop = float(stop)
        prev = None
        count = 0
        # A gap before the first downloaded candle also invalidates the path.
        if (nums.index[0] - first_complete) > pd.Timedelta(minutes=max_gap_minutes):
            return IntradayCheck(None, None, None, current_stop, 0, False,
                                 'The minute-price feed is missing candles before the first reviewable bar.')
        for stamp, r in nums.iterrows():
            if prev is not None and (stamp - prev) > pd.Timedelta(minutes=max_gap_minutes):
                return IntradayCheck(None, None, None, current_stop, count, False,
                                     'Minute-price candles are missing in the interval; event order cannot be verified.')
            o, h, l, c = (float(r[k]) for k in ('Open', 'High', 'Low', 'Close'))
            if not all(map(math.isfinite, (o, h, l, c))) or min(o, h, l, c) <= 0 or h < l:
                return IntradayCheck(None, None, None, current_stop, count, False,
                                     'Invalid minute OHLC data; automatic threshold decision skipped.')
            prev = stamp
            count += 1
            event_time = (stamp + pd.Timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
            # Gap-aware opening fill. Stop-first if both limits occur in one minute.
            if o <= current_stop:
                return IntradayCheck('STOP', o, event_time, current_stop, count, True,
                                     'Price opened at/below paper stop; assumed open fill.')
            if o >= target:
                return IntradayCheck('WIN', o, event_time, current_stop, count, True,
                                     'Price opened at/above paper target; assumed open fill.')
            target_hit, stop_hit = h >= target, l <= current_stop
            if stop_hit:
                return IntradayCheck('STOP', current_stop, event_time, current_stop, count, True,
                                     'Both barriers crossed inside one minute; stop-first conservative assumption.'
                                     if target_hit else 'Minute low crossed the stop level.', target_hit)
            if target_hit:
                return IntradayCheck('WIN', target, event_time, current_stop, count, True,
                                     'Minute high crossed the target level.')
            # Ratchet becomes effective for the next completed candle only.
            if target > entry and current_stop < be_floor and h >= be_trigger and c > be_floor:
                current_stop = float(be_floor)
        # Do not call a partial feed fully reviewed. A quote returned 15
        # minutes late is not evidence that a stop wasn't reached meanwhile.
        session_end = min(now, pd.Timestamp(f'{now.date()} 15:30:00'))
        if (session_end - (nums.index[-1] + pd.Timedelta(minutes=1))) > pd.Timedelta(minutes=max_gap_minutes):
            return IntradayCheck(None, None, None, current_stop, count, False,
                                 'Minute feed has not covered the latest interval; review remains pending.')
        return IntradayCheck(None, None, None, current_stop, count, True,
                             'No target or stop was crossed in the downloaded completed minute candles.')
    except (TypeError, ValueError, KeyError, OverflowError):
        return pending
