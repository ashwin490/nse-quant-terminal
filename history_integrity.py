"""Small, dependency-free safeguards for restoring historical paper-trade records.

Records are never reconstructed from UI counters or invented. A lower current
record count than a previously observed baseline is a reason to review storage.
"""
from __future__ import annotations


def history_state(local_shares: int, expected_minimum: int, cloud_configured: bool,
                  cloud_shares: int | None = None, cloud_failed: bool = False,
                  record_label: str = "share") -> dict:
    noun = "options" if record_label == "option" else "shares"
    label = "option" if record_label == "option" else "share"
    local = max(int(local_shares), 0)
    minimum = max(int(expected_minimum), 0)
    missing = max(0, minimum - local)
    verified = missing == 0
    if verified:
        reason = f"Local historical {label} count meets the configured baseline."
    elif not cloud_configured:
        reason = f"Prior {label} records are missing locally and no Supabase backup is configured."
    elif cloud_failed:
        reason = f"Historical {label} records are missing and the cloud restore returned an error."
    elif cloud_shares is not None and cloud_shares == 0:
        reason = f"Historical {label} records are missing and the configured Supabase source returned no {noun}."
    elif cloud_shares is not None and cloud_shares < minimum:
        reason = f"Supabase currently contains fewer {label} records than the configured history baseline."
    else:
        reason = f"Historical {label} records are missing locally; retry cloud restoration and inspect recovery totals."
    return {
        "verified_count": verified,
        "local": local,
        "minimum": minimum,
        "missing": missing,
        "reason": reason,
        # Prevent new paper allocations based on an incomplete capital snapshot.
        "allow_new_paper_entries": verified,
    }
