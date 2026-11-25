"""Headless NSE paper *research* service; no Streamlit, no broker orders.

Modes:
    python nse_autonomous_worker.py --mode daily
    python nse_autonomous_worker.py --mode monitor
    python nse_autonomous_worker.py --mode health
    python nse_autonomous_worker.py --mode dry-run

On weekdays run daily after NSE close, monitor during trading. A GitHub Actions
schedule is best effort, not guaranteed; the dashboard shows worker age.
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from urllib.parse import quote

import joblib
import numpy as np
import pandas as pd
import requests

from nse_ai_core import (HORIZON, FEATURES, FEATURE_VERSION, clean_ohlc, labelled_samples,
                         latest_features, samples_frame, purged_holdout, select_challenger_temporal,
                         evaluate, constant_baseline, qualifies, forecast_from_model)
from nse_ai_store import ResearchStore

IST = ZoneInfo("Asia/Kolkata")
BASE_UNIVERSE = (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "BHARTIARTL", "ITC",
    "LT", "AXISBANK", "KOTAKBANK", "BAJFINANCE", "MARUTI", "SUNPHARMA", "NTPC",
    "POWERGRID", "ONGC", "COALINDIA", "BEL", "TATAPOWER", "PFC", "RECLTD", "BHEL",
    "TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "WIPRO", "HCLTECH", "TECHM",
    "BPCL", "IOC", "GAIL", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IRCTC",
    "CONCOR", "NHPC", "SJVN", "DABUR", "MARICO", "GODREJCP", "BIOCON",
    "LAURUSLABS", "GRANULES", "NMDC", "SAIL", "JINDALSTEL", "CESC", "PETRONET",
    "INDUSTOWER", "ASHOKLEY", "MOTHERSON", "EXIDEIND", "TRENT", "APOLLOHOSP",
    "ULTRACEMCO", "DRREDDY", "CIPLA", "DIVISLAB", "ADANIPORTS", "GRASIM"
)


def now_ist():
    return datetime.now(IST)


def parse_symbols(env_value=None):
    custom = env_value if env_value is not None else os.getenv("NSE_WORKER_SYMBOLS", "")
    values = custom.replace(";", ",").split(",") if custom.strip() else BASE_UNIVERSE
    output = []
    for token in values:
        s = token.strip().upper().removesuffix(".NS")
        if s and s.replace("&", "").replace("-", "").isalnum() and len(s) <= 25 and s not in output:
            output.append(s)
    if not output:
        raise ValueError("No valid NSE symbols in universe")
    return output


def research_batch(symbols, today: date, cap=35):
    """Rotate deterministically; avoid silently claiming the entire market was scanned."""
    batch = max(1, min(int(cap), len(symbols)))
    index = (today.toordinal()*batch) % len(symbols)
    return (symbols+symbols)[index:index+batch]


def yahoo_bars(symbol, interval="1d", range_="2y", session=None):
    """Yahoo chart OHLC. Fail closed on errors, splits, and invalid OHLC.

    Yahoo is a fallible research provider, NOT an NSE licensed execution feed.
    """
    s = session or requests.Session()
    params = {"interval": interval, "range": range_, "includePrePost": "false", "events": "splits"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol+'.NS',safe='')}"
    last = None
    for attempt in range(3):
        try:
            r = s.get(url, params=params, headers={"User-Agent": "Mozilla/5.0 NSE-research-worker"}, timeout=16)
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(.4*(attempt+1)); continue
            if r.status_code != 200:
                raise RuntimeError(f"Price provider HTTP {r.status_code}")
            payload = r.json().get("chart") or {}
            results = payload.get("result") or []
            if not results or not results[0].get("timestamp"):
                raise ValueError("Missing chart candles")
            raw = results[0]
            if (raw.get("events") or {}).get("splits"):
                # Skipping a split protects historical labels and adjusted entry prices.
                raise ValueError("Corporate split present; cannot establish continuous price units")
            quote_data = (raw.get("indicators", {}).get("quote") or [{}])[0]
            ix = pd.to_datetime(raw["timestamp"], unit="s", utc=True).tz_convert(IST)
            d = pd.DataFrame({k: quote_data.get(k,[]) for k in ("open","high","low","close","volume")}, index=ix)
            if interval == "1d":
                d.index = d.index.tz_localize(None).normalize()
                return clean_ohlc(d)
            # Do not normalize intraday timestamps; keep minute ordering.
            d = d.dropna()
            return d
        except (requests.Timeout, requests.ConnectionError) as ex:
            last = type(ex).__name__
            time.sleep(.4*(attempt+1))
    raise RuntimeError(f"Yahoo data unavailable: {last}")


def backup_history(store, now, *, force=False):
    """Private weekly recovery snapshot of the ORIGINAL paper journals.

    Fails closed if any known historical minimum is missing. Same-project
    backups are not offsite disaster-recovery; export externally as well.
    """
    if not force and now.weekday() != 0:
        return {"status":"NOT_DUE", "frequency":"weekly Monday"}
    shares=store.all_rows("predictions",maximum=30000)
    options=store.all_rows("options_journal",maximum=30000)
    expected_shares=int(os.getenv("NSE_MIN_CLOUD_SHARES","106"))
    expected_options=int(os.getenv("NSE_MIN_CLOUD_OPTIONS","21"))
    if len(shares)<expected_shares or len(options)<expected_options:
        return {"status":"BLOCKED_INCOMPLETE_HISTORY", "shares":len(shares),
                "options":len(options), "expected_shares":expected_shares,
                "expected_options":expected_options}
    stamp=now.strftime('%Y-%m-%d')
    payload={"schema":"original-nse-journal-backup-v1","saved_at":now.isoformat(),
             "shares":shares, "options":options,
             "counts":{"shares":len(shares),"options":len(options)}}
    raw=gzip.compress(json.dumps(payload,sort_keys=True,default=str).encode('utf-8'),compresslevel=6)
    path=f"journal/{stamp}/paper-history.json.gz"
    checksum=store.upload_private("nse-ai-backups",path,raw,"application/gzip")
    return {"status":"SAVED","path":path,"shares":len(shares),"options":len(options),"sha256":checksum}


def monitor_window(now: datetime):
    t = now.astimezone(IST)
    return t.weekday() < 5 and (9,30) <= (t.hour,t.minute) <= (15,30)


def send_alert(message: str):
    url = os.getenv("NSE_ALERT_WEBHOOK_URL", "")
    if not url:
        return False
    if not url.startswith("https://"):
        raise ValueError("HTTPS alert webhook required")
    try:
        requests.post(url, json={"text":"NSE research: " + message[:550]}, timeout=9).raise_for_status()
        return True
    except requests.RequestException:
        return False


def run_health(store, now=None):
    now = now or now_ist()
    last = store.select("nse_ai_runs", columns="run_id,status,finished_at,kind", filters={"kind":"eq.daily","status":"eq.SUCCESS"}, order="finished_at.desc", limit=1)
    if not last:
        return {"ok":False,"reason":"No successful daily learning run"}
    try:
        done = datetime.fromisoformat(last[0]["finished_at"].replace("Z", "+00:00"))
        age = (now-done).total_seconds()/3600
    except (KeyError, TypeError, ValueError):
        return {"ok":False,"reason":"Cannot parse worker heartbeat"}
    max_age_hours = 84 if now.astimezone(IST).weekday() in (0,5,6) else 48
    return {"ok":age <= max_age_hours, "age_hours":round(age, 2), "reason":"Fresh" if age <= max_age_hours else "Daily worker stale"}


def _champion(store):
    state = store.setting("champion", {}) or {}
    if not isinstance(state,dict) or not state.get("model_id"):
        return None, {}
    # Verified integrity before deserialization. Only trusted service-role users
    # may write this private bucket and registry; never load arbitrary URLs.
    raw = store.download_model(state["object_path"], state["sha256"])
    bundle = joblib.load(io.BytesIO(raw))
    if tuple(bundle.get("features", ())) != FEATURES or bundle.get("feature_version") != FEATURE_VERSION:
        raise RuntimeError("Champion feature schema changed; refuse inference")
    return bundle["pipeline"], state


def attempt_retrain(store, samples, now):
    frame = samples_frame(samples)
    if len(frame) < 350:
        return {"status":"COLLECTING", "samples":len(frame), "minimum":350}
    try:
        train, test = purged_holdout(frame)
    except ValueError as exc:
        return {"status":"INSUFFICIENT_VALIDATION", "reason":str(exc), "samples":len(frame)}
    state = store.setting("champion", {}) or {}
    last = store.setting("last_training", {}) or {}
    latest_date = str(train.session_date.max())
    if last.get("train_through") == latest_date:
        return {"status":"UNCHANGED", "train_through":latest_date}
    if last.get("attempt_date"):
        try:
            if (now.date() - date.fromisoformat(last["attempt_date"])).days < 7:
                return {"status":"WEEKLY_COOLDOWN"}
        except (TypeError, ValueError):
            pass
    champion, champion_state = _champion(store)
    # Restrict later challenger evaluations to sessions not used to fit champion.
    if champion is not None:
        earliest = (date.fromisoformat(champion_state["trained_until"]) + timedelta(days=14)).isoformat()
        test = test[test.session_date >= earliest].copy()
        if len(test) < 80 or len(set(test.target_first)) < 2:
            return {"status":"AWAITING_FRESH_OUTCOMES", "fresh_test_rows":len(test)}
    challenger, selection = select_challenger_temporal(train)
    metrics = evaluate(challenger, test)
    baseline = constant_baseline(train, test)
    champ_metrics = evaluate(champion,test) if champion is not None else None
    store.put_setting("last_training", {"attempt_date":now.date().isoformat(),"train_through":latest_date,
                                         "test_through":metrics["through"], "challenger":metrics,
                                         "champion":champ_metrics,"baseline":baseline,"selection":selection})
    if not qualifies(metrics,baseline,champ_metrics):
        return {"status":"REJECTED", "challenger":metrics, "baseline":baseline, "champion":champ_metrics}
    # Serialize immutable artifact, upload before publishing pointer; a failed
    # upload leaves the previous champion in place.
    model_id = f"nse-{now.date().strftime('%Y%m%d')}-{uuid.uuid4().hex[:10]}"
    b = io.BytesIO()
    joblib.dump({"pipeline":challenger, "features":FEATURES, "feature_version":FEATURE_VERSION},b)
    path, sha = store.upload_model(model_id,b.getvalue())
    metadata = {"model_id":model_id, "object_path":path,"sha256":sha,
                "trained_until":latest_date, "tested_until":metrics["through"],
                "metrics":metrics,"baseline":baseline,"champion_metrics":champ_metrics,
                "n_train":len(train),"n_test":len(test),"selection":selection}
    store.insert_immutable("nse_ai_models",[{k:v for k,v in metadata.items() if k!='selection'}],"model_id")
    store.put_setting("champion", metadata)
    return {"status":"PROMOTED", "model_id":model_id, "metrics":metrics}


def daily(store, now=None, fetcher=yahoo_bars):
    now = now or now_ist()
    universe = parse_symbols()
    chosen = research_batch(universe,now.date(),int(os.getenv("NSE_DAILY_SYMBOLS","35")))
    payload = []
    newest = []
    problems = []
    for sym in chosen:
        try:
            hist = fetcher(sym)
            if hist.empty or (now.date()-hist.index[-1].date()).days > 4:
                raise ValueError("No recent price bars")
            payload.extend(labelled_samples(sym,hist))
            features = latest_features(sym,hist)
            if features is not None:
                newest.append(features)
        except Exception as exc:
            problems.append({"symbol":sym,"kind":type(exc).__name__})
    if len(newest) < max(5, len(chosen)//3):
        raise RuntimeError(f"Insufficient trustworthy EOD data ({len(newest)}/{len(chosen)} symbols)")
    # Weekly immutable snapshot; cloud discrepancies are warnings, not a
    # reason to corrupt or reset the independently collected daily features.
    try:
        backup=backup_history(store,now)
    except Exception as exc:
        backup={"status":"FAILED", "error_class":type(exc).__name__}
    if backup["status"] in ("BLOCKED_INCOMPLETE_HISTORY","FAILED"):
        send_alert("Historical paper journal backup failed/incomplete; inspect Supabase counts")
    attempted = store.insert_immutable("nse_ai_samples",payload,"ticker,session_date,horizon,feature_version")
    rows = store.all_samples(maximum=int(os.getenv("NSE_TRAIN_MAX_ROWS","60000")))
    training = attempt_retrain(store, rows, now)
    champ, state = _champion(store)
    predicted = 0
    if champ is not None:
        for latest in newest:
            # A forecast date is the most recent completed trading session,
            # never tomorrow's executable price.
            predicted_row = forecast_from_model(champ,latest,rows)
            predicted_row["model_id"] = state["model_id"]
            store.insert_immutable("nse_ai_forecasts",[predicted_row],"forecast_for,ticker")
            predicted += 1
    return {"symbols_attempted":len(chosen),"usable":len(newest),
            "historical_samples_attempted":attempted, "model":training,
            "new_forecasts_attempted":predicted, "backup":backup, "errors":problems[:10],
            "coverage_note": "Rotating configured universe; not all NSE shares on each daily run"}


def monitor(store, now=None, fetcher=yahoo_bars):
    now = now or now_ist()
    if not monitor_window(now):
        return {"status":"MARKET_CLOSED", "message":"Monitoring not attempted outside NSE hours"}
    positions = store.select("predictions", columns="ticker,predicted_date,entry_price,target_price,stop_loss,status", filters={"status":"eq.ACTIVE"},limit=500)
    marks, issues, touches = [], [], []
    for pos in positions:
        sym = str(pos.get("ticker") or "").upper().removesuffix(".NS")
        if not sym or not pos.get("predicted_date"):
            continue
        try:
            d = fetcher(sym,interval="1m",range_="1d")
            if d.empty:
                raise ValueError("No minute candles")
            minute_time = d.index[-1]
            # Yahoo minutes are timezone-aware IST; the response may be delayed.
            if minute_time.tzinfo is None:
                raise ValueError("Unzoned intraday timestamp")
            age = (now-minute_time.to_pydatetime()).total_seconds()/60
            if age < -3 or age > 20:
                raise ValueError("Stale intraday bar")
            bar = d.iloc[-1]
            target, stop = float(pos["target_price"]), float(pos["stop_loss"])
            touched_target = bool((d.high >= target).any())
            touched_stop = bool((d.low <= stop).any())
            note = "AMBIGUOUS_TOUCH" if (touched_target and touched_stop) else ("TARGET_TOUCH" if touched_target else ("STOP_TOUCH" if touched_stop else "NO_TOUCH"))
            mark = {"ticker":sym,"predicted_date":str(pos["predicted_date"])[:10],
                    "observed_at":now.isoformat(),"last_price":round(float(bar.close),5),
                    "quote_age_minutes":round(age,2), "status":note}
            marks.append(mark)
            if note != "NO_TOUCH":
                touches.append(sym+":"+note)
        except Exception as exc:
            issues.append({"symbol":sym,"kind":type(exc).__name__})
    new_touches=[]
    if marks:
        store.upsert("nse_ai_monitor_marks", marks, "ticker,predicted_date")
        significant = [m for m in marks if m["status"] != "NO_TOUCH"]
        if significant:
            events = [{**m, "event_id": hashlib.sha256(
                (m["ticker"]+m["predicted_date"]+m["status"]+now.date().isoformat()).encode()
                ).hexdigest()} for m in significant]
            for event in events:
                prior=store.select("nse_ai_monitor_events",columns="event_id",
                                   filters={"event_id":"eq."+event["event_id"]},limit=1)
                if not prior:
                    new_touches.append(event["ticker"]+":"+event["status"])
            store.insert_immutable("nse_ai_monitor_events", events, "event_id")
    if new_touches:
        send_alert("Observed threshold touches (NOT verified fills): "+", ".join(new_touches[:12]))
    # Never overwrite the paper ledger, decide exit time, or manufacture a fill.
    return {"open_positions":len(positions),"fresh_marks":len(marks),
            "coverage_incomplete":len(issues),"possible_touches":touches[:20],
            "issues":issues[:12],"paper_ledger_modified":False}


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=["daily","monitor","health","dry-run"])
    args=p.parse_args(argv)
    if args.mode == "dry-run":
        # Does not hit the network or require credentials.
        print(json.dumps({"mode":"dry-run", "symbols":len(parse_symbols()),
                          "daily_batch":research_batch(parse_symbols(),date(2026,11,25))[:5],
                          "feature_version":FEATURE_VERSION,"no_live_orders":True}))
        return 0
    store = ResearchStore()
    now = now_ist()
    run_id = (f"{args.mode}-{now:%Y%m%d}" if args.mode=="daily" else
              f"{args.mode}-{now:%Y%m%d-%H}{(now.minute//15)*15:02d}")
    stamp = now.isoformat()
    store.upsert("nse_ai_runs",[{"run_id":run_id,"kind":args.mode,"status":"STARTED","started_at":stamp,"details":{}}],"run_id")
    try:
        if args.mode == "daily":
            details=daily(store,now)
            if not details["new_forecasts_attempted"]:
                send_alert("Daily learning ran but no validated ML champion forecasts yet; inspect model diagnostics")
        elif args.mode == "monitor":
            details=monitor(store,now)
        else:
            details=run_health(store,now)
        status="SUCCESS" if args.mode!="health" or details.get("ok") else "DEGRADED"
        if status == "DEGRADED":
            send_alert("Daily learning heartbeat unhealthy; review last successful run and data provider")
        store.upsert("nse_ai_runs",[{"run_id":run_id,"kind":args.mode,"status":status,
                                    "finished_at":now_ist().isoformat(),"details":details}],"run_id")
        print(json.dumps({"mode":args.mode,"status":status,"details":details},default=str))
        return 0 if status=="SUCCESS" else 2
    except Exception as exc:
        err=type(exc).__name__
        try:
            store.upsert("nse_ai_runs",[{"run_id":run_id,"kind":args.mode,"status":"FAILED",
                                        "finished_at":now_ist().isoformat(),"error_class":err,
                                        "details":{"message":"Fail closed; no ledger modifications"}}],"run_id")
        except Exception:
            pass
        send_alert(f"{args.mode} FAILED ({err}); review GitHub Actions logs")
        print(json.dumps({"mode":args.mode,"status":"FAILED","error_class":err}),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
