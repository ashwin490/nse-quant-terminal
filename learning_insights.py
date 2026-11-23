"""Per-stock *observations* from logged results; not claims of causal AI learning."""
from __future__ import annotations
import json
import math
import pandas as pd


def _outcome(value):
    name = str(value or '').upper()
    if 'WIN' in name:
        return 'WIN'
    if 'LOSS' in name:
        return 'LOSS'
    if 'BREAK-EVEN' in name:
        return 'BE'
    if 'EXPIRED' in name:
        return 'EXPIRED'
    if 'ACTIVE' in name:
        return 'ACTIVE'
    return 'UNKNOWN'


def features_of(raw):
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw) if raw else {}
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def is_training_ready(row):
    """A plausible feature snapshot and decided outcome are necessary, not sufficient.
    Actual retraining still applies its own stricter validation and time split.
    """
    if _outcome(row.get('status')) not in ('WIN','LOSS'):
        return False
    f = features_of(row.get('features_json'))
    raw = f.get('raw_features') or {}
    req = ('dist_ema20_pct', 'trend_spread_pct', 'atr_pct', 'rvol', 'rsi_14', 'deliv_shock')
    try:
        return isinstance(raw, dict) and all(math.isfinite(float(raw[k])) for k in req)
    except (KeyError, TypeError, ValueError):
        return False


def describe_trade(row) -> dict:
    """Make a plain-English observation for each paper trade; avoid false attribution."""
    outcome = _outcome(row.get('status'))
    f = features_of(row.get('features_json'))
    raw = f.get('raw_features') or {}
    date_in = pd.to_datetime(row.get('timestamp') or row.get('date_str'), errors='coerce')
    date_out = pd.to_datetime(row.get('exit_timestamp'), errors='coerce')
    days = int((date_out - date_in).days) if pd.notna(date_in) and pd.notna(date_out) and date_out >= date_in else None
    has_features = is_training_ready(row)
    try:
        pnl = float(row.get('pnl_pct'))
    except (ValueError, TypeError):
        pnl = None
    if outcome == 'WIN':
        observation = 'The journal records a profitable target exit. Check whether similar conditions repeat out of sample.'
    elif outcome == 'LOSS':
        observation = 'The journal records a loss. Compare entry volatility, trend and stop distance; the true cause is unproven.'
    elif outcome == 'BE':
        observation = 'The position was closed near break-even. Costs and slippage may still make net returns negative.'
    elif outcome == 'EXPIRED':
        observation = 'The journal records an expiry/time exit. This alone does not prove the price target was impossible.'
    elif outcome == 'ACTIVE':
        observation = 'Still open. This is not a completed training outcome.'
    else:
        observation = 'Historical outcome is unclassified and should not be used as a training label.'
    conditions = []
    for key, label in [('atr_pct','Volatility at entry'), ('rsi_14','RSI at entry'), ('rvol','Relative volume')]:
        v = raw.get(key, f.get(key)) if isinstance(raw, dict) else f.get(key)
        try:
            num = float(v)
            if math.isfinite(num):
                conditions.append(f'{label}: {num:.1f}' + ('%' if key == 'atr_pct' else ''))
        except (TypeError, ValueError):
            pass
    learned = ('Candidate for historical training/evaluation after full validation.' if has_features else
               'Not currently a complete validated training example.')
    return {'outcome':outcome,'pnl':pnl,'days_held':days,
            'observation':observation,'conditions':' · '.join(conditions) or 'No entry-feature snapshot saved',
            'training_note':learned,'potential_example':has_features}


def stock_history_summary(rows: pd.DataFrame) -> dict:
    if rows is None or rows.empty:
        return {'total':0,'open':0,'finished':0,'wins':0,'losses':0,'decided':0,
                'win_rate':None,'avg_pnl':None,'potential_training':0}
    labels = rows['status'].apply(_outcome)
    finished = labels.isin(('WIN','LOSS','BE','EXPIRED'))
    decided = labels.isin(('WIN','LOSS'))
    pnl = pd.to_numeric(rows.loc[finished,'pnl_pct'], errors='coerce') if 'pnl_pct' in rows else pd.Series(dtype=float)
    return {'total':len(rows), 'open':int((labels=='ACTIVE').sum()), 'finished':int(finished.sum()),
            'wins':int((labels=='WIN').sum()), 'losses':int((labels=='LOSS').sum()),
            'decided':int(decided.sum()), 'win_rate':round(100 * int((labels=='WIN').sum()) / int(decided.sum()),1) if decided.any() else None,
            'avg_pnl':round(float(pnl.mean()),2) if pnl.notna().any() else None,
            'potential_training':sum(bool(is_training_ready(r)) for r in rows.to_dict('records'))}


def stock_evidence(rows: pd.DataFrame) -> list[str]:
    """Describe observed patterns without pretending a model learned or proved causality."""
    if rows is None or rows.empty:
        return ["No past paper results for this symbol yet."]
    recorded = rows.to_dict('records')
    decided = [r for r in recorded if _outcome(r.get('status')) in ('WIN','LOSS')]
    wins = [r for r in decided if _outcome(r.get('status')) == 'WIN']
    losses = [r for r in decided if _outcome(r.get('status')) == 'LOSS']
    notes = []
    if not decided:
        return ["There are no confirmed wins/losses for this symbol. Open or unclassified records cannot establish performance."]
    notes.append(f"This symbol has {len(wins)} recorded wins and {len(losses)} recorded losses. "
                 "Past journal outcomes are not evidence that future predictions will work.")
    for label, group in [('Winners', wins), ('Losing positions', losses)]:
        if group:
            numbers = pd.to_numeric(pd.Series([r.get('pnl_pct') for r in group]), errors='coerce').dropna()
            if len(numbers):
                notes.append(f"{label}: average recorded net paper result {float(numbers.mean()):+.1f}% over {len(numbers)} positions.")
    if len(wins) >= 3 and len(losses) >= 3:
        for field, name in [('atr_pct','entry volatility'), ('rvol','relative trading volume')]:
            def average_feature(group):
                values = []
                for r in group:
                    f = features_of(r.get('features_json'))
                    raw = f.get('raw_features') if isinstance(f.get('raw_features'), dict) else f
                    try:
                        v = float(raw.get(field))
                        if math.isfinite(v):
                            values.append(v)
                    except (TypeError, ValueError):
                        pass
                return sum(values)/len(values) if len(values) >= 3 else None
            a,b = average_feature(wins),average_feature(losses)
            if a is not None and b is not None:
                notes.append(f"Observed {name}: {a:.2f} on wins versus {b:.2f} on losses. "
                             "This association is not a verified cause or proven model feature importance.")
    elif len(decided) < 10:
        notes.append("Very small per-symbol sample: avoid claiming a dependable learned pattern from these results.")
    notes.append("Actual AI model updates require enough validated features, time-ordered holdout tests and successful promotion checks.")
    return notes


def forecast_review(row) -> str:
    """Compare a genuinely saved entry-time timeline with the recorded outcome."""
    f = features_of(row.get('features_json'))
    saved = f.get('timeline_at_entry')
    if not isinstance(saved, dict):
        return 'No timeline was saved when this trade was opened; historical forecasts will not be invented.'
    horizon = saved.get('horizon_sessions')
    forecast = saved.get('label') or 'Timing not established'
    obs = describe_trade(row)
    d = obs.get('days_held')
    outcome = obs.get('outcome')
    held = f'{d} calendar day(s)' if d is not None else 'unknown holding period'
    return (f"At entry: {forecast}, in a {horizon}-trading-session study. "
            f"Recorded: {outcome} after {held}. "
            "Different calendar/trading-day units mean this is not an exact accuracy comparison.")
