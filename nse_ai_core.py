"""Independent, auditable, leakage-conscious daily NSE equity research.

All labels are calculated *after* the horizon closes. This is research-only:
Yahoo OHLC are not exchange-verified executable prices and same-day bar
collisions conservatively count the stop as first. No broker orders.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

FEATURES = ("ret5", "ret20", "ema_gap", "rsi14", "atr14", "vol20", "rel_vol", "distance_20d_high")
HORIZON = 5
TARGET_PCT = 5.0
STOP_PCT = 3.0
COST_PCT = 0.28
FEATURE_VERSION = 1


def clean_ohlc(data: pd.DataFrame) -> pd.DataFrame:
    """Validate daily inputs; reject malformed/duplicated and impossible bars."""
    if data is None or data.empty:
        return pd.DataFrame()
    df = data.copy()
    df.columns = [str(x).lower() for x in df.columns]
    required = ("open", "high", "low", "close", "volume")
    if not all(k in df.columns for k in required):
        raise ValueError("Missing OHLCV columns")
    df = df.loc[:, list(required)]
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None).normalize()
    if df.index.duplicated().any():
        raise ValueError("Duplicated session dates")
    df = df.sort_index()
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna()
    ok = ((df[["open", "high", "low", "close"]] > 0).all(axis=1)
          & (df["volume"] >= 0)
          & (df["low"] <= df[["open", "close"]].min(axis=1))
          & (df["high"] >= df[["open", "close"]].max(axis=1)))
    if not ok.all():
        raise ValueError("Invalid OHLC bars (nonpositive, inverted or out of bounds)")
    return df


def features_for_daily(df: pd.DataFrame) -> pd.DataFrame:
    d = clean_ohlc(df)
    if d.empty:
        return pd.DataFrame()
    c, hi, lo, v = d.close, d.high, d.low, d.volume
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    tr = pd.concat([hi-lo, (hi-c.shift()).abs(), (lo-c.shift()).abs()], axis=1).max(axis=1)
    out = pd.DataFrame(index=d.index)
    out["ret5"] = c.pct_change(5) * 100
    out["ret20"] = c.pct_change(20) * 100
    out["ema_gap"] = (c / c.ewm(span=20, adjust=False).mean() - 1) * 100
    out["rsi14"] = rsi.fillna(100).clip(0, 100)
    out["atr14"] = (tr.rolling(14, min_periods=14).mean() / c) * 100
    out["vol20"] = c.pct_change().rolling(20).std() * 100
    out["rel_vol"] = (v / v.shift().rolling(20).mean()).clip(0, 20)
    out["distance_20d_high"] = (c / hi.shift().rolling(20).max() - 1) * 100
    return out.replace([np.inf, -np.inf], np.nan)


def first_touch(future: pd.DataFrame, entry: float, target_pct=TARGET_PCT,
                stop_pct=STOP_PCT):
    """Target first / stop first / time exit; ambiguous same-day => stop first."""
    target = entry * (1 + target_pct / 100)
    stop = entry * (1 - stop_pct / 100)
    for i, (_, bar) in enumerate(future.iterrows(), 1):
        if float(bar["low"]) <= stop:  # conservative ordering on a shared bar
            return "STOP", i, -stop_pct - COST_PCT
        if float(bar["high"]) >= target:
            return "TARGET", i, target_pct - COST_PCT
    if len(future) == 0:
        raise ValueError("Cannot label a position with no future bars")
    return "TIME", len(future), (float(future.iloc[-1]["close"]) / entry - 1) * 100 - COST_PCT


def labelled_samples(ticker: str, daily: pd.DataFrame, max_entries: int = 160):
    """Matured labels only; last HORIZON days never get assigned outcomes."""
    d = clean_ohlc(daily)
    if len(d) < 70:
        return []
    features = features_for_daily(d)
    rows = []
    for i in range(max(30, len(d)-HORIZON-max_entries), len(d)-HORIZON):
        vect = features.iloc[i]
        if not np.isfinite(vect.to_numpy(dtype=float)).all():
            continue
        entry = float(d.iloc[i].close)
        label, days, net = first_touch(d.iloc[i+1:i+1+HORIZON], entry)
        rows.append({
            "ticker": ticker,
            "session_date": d.index[i].date().isoformat(),
            "horizon": HORIZON,
            "feature_version": FEATURE_VERSION,
            "features": {k: round(float(vect[k]), 7) for k in FEATURES},
            "entry_price": round(entry, 5),
            "outcome": label,
            "target_first": label == "TARGET",
            "days_to_exit": int(days),
            "net_return_pct": round(float(net), 5),
        })
    return rows


def latest_features(ticker: str, daily: pd.DataFrame) -> dict | None:
    d = clean_ohlc(daily)
    if len(d) < 45:
        return None
    features = features_for_daily(d).iloc[-1]
    if not np.isfinite(features.to_numpy(dtype=float)).all():
        return None
    return {
        "ticker": ticker,
        "session_date": d.index[-1].date().isoformat(),
        "entry_price": round(float(d.iloc[-1].close), 5),
        "features": {k: round(float(features[k]), 7) for k in FEATURES},
    }


def samples_frame(rows: list[dict]) -> pd.DataFrame:
    prepared = []
    for r in rows:
        f = r.get("features") or {}
        if not isinstance(f, dict) or any(k not in f for k in FEATURES):
            continue
        try:
            d = {k: float(f[k]) for k in FEATURES}
            if not all(math.isfinite(x) for x in d.values()):
                continue
            if int(r.get("feature_version", -1)) != FEATURE_VERSION:
                continue
            d.update({"session_date": str(r["session_date"]), "target_first": int(r["target_first"])})
            prepared.append(d)
        except (ValueError, TypeError, KeyError):
            continue
    if not prepared:
        return pd.DataFrame(columns=[*FEATURES, "session_date", "target_first"])
    return pd.DataFrame(prepared).sort_values("session_date", kind="stable").reset_index(drop=True)


def purged_holdout(frame: pd.DataFrame, gap_sessions: int = HORIZON+2):
    """Date-blocked split. Same-session symbols cannot leak across train/test."""
    unique = sorted(frame.session_date.unique().tolist())
    n = len(unique)
    if n < 65:
        raise ValueError("Insufficient distinct training sessions for purged holdout")
    boundary = int(n * 0.8)
    if boundary - gap_sessions < 30 or n-boundary < 12:
        raise ValueError("Holdout sessions too small")
    train = frame[frame.session_date <= unique[boundary-gap_sessions-1]].copy()
    test = frame[frame.session_date >= unique[boundary]].copy()
    if len(train) < 200 or len(test) < 80:
        raise ValueError("Insufficient labelled examples for temporal validation")
    if len(set(train.target_first)) < 2 or len(set(test.target_first)) < 2:
        raise ValueError("Both outcome classes required in train and holdout")
    return train, test


def evaluate(model, holdout: pd.DataFrame) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    y = holdout.target_first.to_numpy(dtype=int)
    p = np.clip(model.predict_proba(holdout[list(FEATURES)])[:, 1], 1e-5, 1-1e-5)
    return {"brier": round(float(brier_score_loss(y,p)), 6),
            "log_loss": round(float(log_loss(y,p)), 6),
            "auc": round(float(roc_auc_score(y,p)), 6),
            "n": len(y), "positive_rate": round(float(y.mean()), 6),
            "through": str(holdout.session_date.max())}


def constant_baseline(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    from sklearn.metrics import brier_score_loss, log_loss
    p = float(np.clip(train.target_first.mean(), .01, .99))
    y = test.target_first.to_numpy(dtype=int)
    return {"brier": round(float(brier_score_loss(y, [p]*len(y))), 6),
            "log_loss": round(float(log_loss(y, [p]*len(y))), 6),
            "n": len(y), "prior": round(p, 6)}


def fit_challenger(train: pd.DataFrame):
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    # Simple regularised model offers an explainable, reproducible baseline.
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=0.15, max_iter=1000, class_weight=None, random_state=42))
    model.fit(train[list(FEATURES)], train.target_first.astype(int))
    return model


def select_challenger_temporal(train: pd.DataFrame):
    """Pick linear/nonlinear family on an EARLIER, purged validation block.

    The final holdout is kept entirely unseen until model-family selection ends.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    unique=sorted(train.session_date.unique().tolist())
    b=int(len(unique)*.78)
    fit_rows=train[train.session_date <= unique[max(0,b-HORIZON-3)]].copy()
    val_rows=train[train.session_date >= unique[b]].copy()
    if len(fit_rows)<160 or len(val_rows)<50 or len(set(fit_rows.target_first))<2 or len(set(val_rows.target_first))<2:
        return fit_challenger(train), {"algorithm":"regularised_logistic", "selection":"insufficient earlier validation"}
    candidates={
        "regularised_logistic": lambda: fit_challenger(fit_rows),
        "hist_gradient_boosting": lambda: HistGradientBoostingClassifier(
            learning_rate=.03,max_iter=120,max_leaf_nodes=10,min_samples_leaf=30,
            l2_regularization=2.0,early_stopping=False,random_state=42).fit(
                fit_rows[list(FEATURES)],fit_rows.target_first.astype(int))}
    scored=[]
    for name, factory in candidates.items():
        fitted=factory()
        score=evaluate(fitted,val_rows)
        scored.append((score['brier'],score['log_loss'],name,score))
    _, _, winner, score=min(scored,key=lambda x:(x[0],x[1]))
    if winner=='hist_gradient_boosting':
        final=HistGradientBoostingClassifier(learning_rate=.03,max_iter=120,max_leaf_nodes=10,
            min_samples_leaf=30,l2_regularization=2.0,early_stopping=False,random_state=42)
        final.fit(train[list(FEATURES)],train.target_first.astype(int))
    else:
        final=fit_challenger(train)
    return final, {"algorithm":winner,"validation":score,"families_checked":len(scored)}


def qualifies(metrics: dict, baseline: dict, champion: dict | None = None):
    """Require material improvement on unseen samples; never promote on count alone."""
    benchmark = champion if champion else baseline
    return (metrics["n"] >= 80
            and metrics["brier"] + 0.003 < benchmark["brier"]
            and metrics["log_loss"] < benchmark["log_loss"] * 0.995
            and metrics["brier"] < baseline["brier"] - 0.003)


def forecast_from_model(model, latest: dict, history: list[dict] | None = None) -> dict:
    x = pd.DataFrame([latest["features"]], columns=list(FEATURES))
    p = float(np.clip(model.predict_proba(x)[0, 1], .01, .99))
    # Conservative lower-bound EV assumption: all non-targets treated as stop losses.
    lower_bound_ev = p * (TARGET_PCT-COST_PCT) + (1-p)*(-STOP_PCT-COST_PCT)
    success_days = [int(r.get("days_to_exit",0)) for r in (history or [])
                    if r.get("outcome") == "TARGET" and r.get("ticker")==latest["ticker"]
                    and int(r.get("days_to_exit",0)) > 0]
    timing = (f"{max(1, int(np.percentile(success_days, 20)))}–"
              f"{max(1, int(np.percentile(success_days, 80)))} sessions"
              if len(success_days) >= 10 else "Insufficient ticker history")
    entry = latest["entry_price"]
    return {"forecast_for": latest["session_date"], "ticker": latest["ticker"],
            "entry_price": entry,
            "target_price": round(entry*(1+TARGET_PCT/100), 4),
            "stop_loss": round(entry*(1-STOP_PCT/100), 4),
            "target_first_probability": round(p, 5),
            "conservative_ev_pct": round(lower_bound_ev, 4),
            "time_estimate": timing,
            "horizon_sessions": HORIZON,
            "decision": "RESEARCH" if lower_bound_ev >= .2 else "WATCH",
            "note": "EOD next-session research; not an executable quote or guaranteed probability."}
