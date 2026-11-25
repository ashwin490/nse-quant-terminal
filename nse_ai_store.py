"""Minimal fail-closed Supabase REST client for an independent research worker.

No anonymous writes, no accidental ledger writes, no dynamic SQL, no browser
session storage. Model artifacts remain in a private Storage bucket.
"""
from __future__ import annotations
import hashlib
import os
import time
from urllib.parse import quote

import requests


class ResearchStore:
    def __init__(self, url=None, service_key=None, session=None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/").split("/rest/v1")[0]
        self.key = service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not self.url.startswith("https://") or len(self.key) < 20:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required; fail closed")
        self.http = session or requests.Session()
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                        "Accept": "application/json"}

    def _call(self, method, path, *, params=None, json=None, data=None, extra=None, timeout=30):
        h = dict(self.headers)
        if extra:
            h.update(extra)
        # Do not print authorization headers, URLs with sensitive query strings,
        # or raw remote response data to CI logs.
        last = None
        for attempt in range(3):
            try:
                resp = self.http.request(method, self.url + path, params=params,
                                         json=json, data=data, headers=h, timeout=timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {resp.status_code}"
                    if attempt < 2:
                        time.sleep(1.5*(attempt+1))
                        continue
                if not 200 <= resp.status_code < 300:
                    raise RuntimeError(f"Supabase {path.split('?')[0].split('/')[-1]} HTTP {resp.status_code}")
                return resp
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = type(exc).__name__
                if attempt < 2:
                    time.sleep(1.5*(attempt+1))
                    continue
        raise RuntimeError(f"Supabase request failed ({last})")

    def select(self, table, *, columns="*", filters=None, order=None, limit=1000, offset=0):
        allowed = {"nse_ai_samples", "nse_ai_forecasts", "nse_ai_models", "nse_ai_settings",
                   "nse_ai_runs", "nse_ai_monitor_marks", "nse_ai_monitor_events", "predictions", "options_journal"}
        if table not in allowed:
            raise ValueError("Disallowed table")
        p = {"select": columns, "limit": str(limit), "offset": str(offset)}
        for k,v in (filters or {}).items():
            if not k.replace("_", "").isalnum():
                raise ValueError("Invalid filter column")
            p[k] = str(v)
        if order:
            p["order"] = order
        return self._call("GET", f"/rest/v1/{table}", params=p).json()

    def all_samples(self, maximum=60000):
        output = []
        for start in range(0, maximum, 1000):
            batch = self.select("nse_ai_samples", columns="ticker,session_date,feature_version,features,target_first,outcome,days_to_exit", limit=1000,offset=start,order="session_date.asc")
            output.extend(batch)
            if len(batch) < 1000:
                break
        if len(output) >= maximum:
            raise RuntimeError("Sample cap reached; export/archive before training; never silently truncate")
        return output

    def insert_immutable(self, table, rows, conflict, batch_size=100):
        if not rows:
            return 0
        total = 0
        for start in range(0, len(rows), batch_size):
            group = rows[start:start+batch_size]
            self._call("POST", f"/rest/v1/{table}", params={"on_conflict": conflict}, json=group,
                       extra={"Prefer": "resolution=ignore-duplicates,return=minimal"})
            total += len(group)
        return total  # attempted rows, NOT guaranteed novel (upsert ignores duplicates)

    def upsert(self, table, rows, conflict):
        if not rows:
            return
        self._call("POST", f"/rest/v1/{table}", params={"on_conflict":conflict}, json=rows,
                   extra={"Prefer":"resolution=merge-duplicates,return=minimal"})

    def setting(self, name, default=None):
        rows = self.select("nse_ai_settings", filters={"key": "eq."+name}, limit=1)
        return rows[0]["value"] if rows else default

    def put_setting(self, name, value):
        self.upsert("nse_ai_settings",[{"key":name,"value":value}], "key")

    def all_rows(self, table, maximum=10000):
        """Fetch full table for consistent count checks; fail if cap could truncate."""
        output=[]
        for start in range(0,maximum,1000):
            batch=self.select(table,limit=min(1000,maximum-start),offset=start)
            output.extend(batch)
            if len(batch)<min(1000,maximum-start):
                return output
        # Exact-limit results may be complete, but do not assume that.
        raise RuntimeError("Backup row cap reached; refusing incomplete snapshot")

    def upload_private(self, bucket, path, raw: bytes, content_type="application/octet-stream"):
        if bucket not in ("nse-ai-models","nse-ai-backups") or ".." in path or path.startswith("/"):
            raise ValueError("Unexpected private bucket/path")
        self._call("POST", f"/storage/v1/object/{bucket}/"+quote(path,safe="/"),data=raw,
                   extra={"Content-Type":content_type,"x-upsert":"false"},timeout=90)
        return hashlib.sha256(raw).hexdigest()

    def upload_model(self, model_id, raw: bytes):
        name = f"champion/{quote(model_id, safe='')}.joblib"
        self._call("POST", "/storage/v1/object/nse-ai-models/"+name, data=raw,
                   extra={"Content-Type":"application/octet-stream","x-upsert":"false"}, timeout=60)
        return name, hashlib.sha256(raw).hexdigest()

    def download_model(self, path, expected_hash):
        if not path.startswith("champion/") or ".." in path:
            raise ValueError("Unexpected model path")
        content = self._call("GET", "/storage/v1/object/nse-ai-models/"+quote(path,safe="/"), timeout=60).content
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("Model integrity mismatch")
        return content
