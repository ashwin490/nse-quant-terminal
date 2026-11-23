"""Learning-tile text derived from recorded trades; not a causal AI explanation."""
from __future__ import annotations
import math
from learning_insights import describe_trade, features_of


def _finite(val):
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def build_learning_tile(row: dict) -> dict:
    """One event per card. Show potential learning, never claim actual retraining."""
    obs = describe_trade(row)
    status = obs['outcome']
    fields = features_of(row.get('features_json'))
    raw = fields.get('raw_features') or fields
    if not isinstance(raw, dict):
        raw = {}
    atr = _finite(raw.get('atr_pct'))
    rsi = _finite(raw.get('rsi_14'))
    rvol = _finite(raw.get('rvol'))
    measures = []
    if atr is not None:
        measures.append(f'{atr:.1f}% entry volatility')
    if rsi is not None:
        measures.append(f'RSI {rsi:.1f}')
    if rvol is not None:
        measures.append(f'{rvol:.1f}× relative volume')
    context = ', '.join(measures) if measures else 'No reliable entry-indicator snapshot was saved'

    if status == 'WIN':
        headline = 'Target outcome recorded'
        follow_up = (f'Test whether setups with {context} also succeed on later, unseen periods. '
                     'One win alone cannot establish a dependable pattern.')
    elif status == 'LOSS':
        headline = 'Loss outcome recorded'
        follow_up = (f'Compare losing setups with {context} against winning setups, including stop distance and price gaps. '
                     'This observation does not identify the cause of the loss.')
    elif status in ('BE', 'EXPIRED'):
        headline = 'Exit without a confirmed target win'
        follow_up = ('Check whether holding-period rules, protective stops, and estimated costs explain similar outcomes '
                     'across many validated trades.')
    elif status == 'ACTIVE':
        headline = 'Still being monitored'
        follow_up = 'Wait for a confirmed exit before treating this as a labelled training example.'
    else:
        headline = 'Outcome needs classification'
        follow_up = 'Do not train on this row until the recorded outcome and its evidence are validated.'

    if status in ('WIN', 'LOSS') and not obs['potential_example']:
        follow_up += ' The required entry-feature snapshot is missing or incomplete.'
    pnl = obs['pnl']
    if pnl is not None and not math.isfinite(pnl):
        pnl = None
    return {
        'symbol': str(row.get('ticker') or 'Unknown share'),
        'date': str(row.get('date_str') or row.get('timestamp') or 'Unknown date')[:10],
        'status': status,
        'pnl': pnl,
        'headline': headline,
        'observation': obs['observation'],
        'entry_context': context,
        'next_test': follow_up,
        'training_candidate': bool(obs['potential_example']),
        'hold_days': obs['days_held'],
    }
