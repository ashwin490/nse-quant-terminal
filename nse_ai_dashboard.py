"""Pure, non-trading display helpers for the independent NSE daily learner."""
from __future__ import annotations
from datetime import datetime, timezone


def worker_status(last_run, now=None):
    now = now or datetime.now(timezone.utc)
    if not last_run:
        return {"state":"NOT_CONFIGURED", "label":"Daily AI worker has not reported yet"}
    try:
        end = datetime.fromisoformat(str(last_run["finished_at"]).replace("Z","+00:00"))
        age = (now-end).total_seconds()/3600
        # Do not falsely alarm on Sat/Sun or Monday morning: no weekday
        # after-market learning job is scheduled during the weekend.
        ist_now = now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))
        permitted_age = 84 if ist_now.weekday() in (0,5,6) else 48
        state = ("HEALTHY" if age <= permitted_age and last_run.get("status")=="SUCCESS" else
                 "FAILED" if last_run.get("status") == "FAILED" else "STALE")
        return {"state":state,"age_hours":max(0,round(age,1)),
                "label":f"{state} · last run {max(0,round(age,1))} hours ago"}
    except (TypeError,KeyError,ValueError):
        return {"state":"UNKNOWN","label":"Worker status unavailable"}


def valid_forecasts(rows, model_id, max_cards=5):
    """Never render a price or 'AI' label from incomplete/unvalidated rows."""
    valid=[]
    for r in rows or []:
        try:
            if not model_id or str(r.get("model_id")) != str(model_id):
                continue
            e,t,s=float(r["entry_price"]),float(r["target_price"]),float(r["stop_loss"])
            p=float(r["target_first_probability"])
            if not (0< s < e < t and 0 <= p <= 1):
                continue
            if int(r.get("horizon_sessions",0)) <= 0:
                continue
            valid.append(r)
        except (ValueError,TypeError,KeyError):
            continue
    return valid[:max_cards]
