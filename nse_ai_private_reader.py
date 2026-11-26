"""Server-side, GET-only access to the NSE autonomous research status tables.

The credentials are loaded by the Streamlit server, never rendered in UI.
This code does not give a Supabase secret key read-only permissions: secret keys
are privileged, even though this module performs GET requests only.
"""
from __future__ import annotations
import requests


class PrivateResearchReadError(RuntimeError):
    """Sanitised read failure: never include keys, URLs or response bodies."""


def _read_table(base_url: str, key: str, table: str, *, select: str, params: dict):
    if table not in {"nse_ai_settings", "nse_ai_runs", "nse_ai_forecasts"}:
        raise ValueError("Disallowed research table")
    url = (base_url or "").strip().split("/rest/v1")[0].rstrip("/")
    credential = (key or "").strip()
    if not url.startswith("https://") or not credential:
        raise PrivateResearchReadError("missing_project_or_key")
    headers = {"apikey": credential, "Accept": "application/json"}
    # Supabase's newer sb_secret_ format is not a JWT. Legacy service_role
    # keys remain JWTs, which REST APIs also accept in Authorization.
    if not credential.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + credential
    try:
        response = requests.get(f"{url}/rest/v1/{table}", headers=headers,
                                params={"select": select, **params}, timeout=12)
        if response.status_code in (401, 403):
            raise PrivateResearchReadError("permission_denied")
        if not (200 <= response.status_code < 300):
            raise PrivateResearchReadError("read_failed")
        data = response.json()
        if not isinstance(data, list):
            raise PrivateResearchReadError("invalid_response")
        return data
    except requests.exceptions.RequestException as exc:
        raise PrivateResearchReadError("network_failed") from exc
    except ValueError as exc:
        if isinstance(exc, PrivateResearchReadError):
            raise
        raise PrivateResearchReadError("invalid_response") from exc


def private_research_snapshot(url: str, key: str) -> dict:
    """Read status + latest forecasts without accessing or changing ledger tables."""
    settings = _read_table(url, key, "nse_ai_settings", select="value",
                           params={"key": "eq.champion", "limit": "1"})
    runs = _read_table(url, key, "nse_ai_runs",
                       select="run_id,status,finished_at,started_at,kind",
                       params={"kind": "eq.daily", "order": "started_at.desc", "limit": "1"})
    forecasts = _read_table(url, key, "nse_ai_forecasts",
                            select="forecast_for,ticker,model_id,entry_price,target_price,stop_loss,target_first_probability,conservative_ev_pct,time_estimate,horizon_sessions,decision",
                            params={"order": "forecast_for.desc", "limit": "80"})
    return {"champion": (settings[0].get("value") if settings else {}) or {},
            "run": runs[0] if runs else None,
            "forecasts": forecasts}
