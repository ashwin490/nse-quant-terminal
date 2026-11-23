"""Read-only diagnostics for NSE stock and option scans.

Never fabricates quotes, contract availability, or paper positions.  The UI
can distinguish a batch *selected* from actual NSE option-chain responses.
"""
from __future__ import annotations


def new_option_scan_health(selected: int = 0, universe: int = 0) -> dict:
    return {
        "selected": max(0, int(selected)),
        "universe": max(0, int(universe)),
        "price_histories": 0,
        "directional": 0,
        "chains_received": 0,
        "fresh_chains": 0,
        "expiry_matches": 0,
        "missing_lots": 0,
        "contract_reviews": 0,
        "consider": 0,
        "watch": 0,
        "avoid": 0,
        "http_status_counts": {},
    }


def option_scan_summary(stats: dict) -> str:
    n = lambda k: max(0, int(stats.get(k, 0)))
    base = (f"{n('selected')} of {n('universe')} F&O underlyings selected for this rotating batch · "
            f"{n('price_histories')} with price history · "
            f"{n('directional')} passed movement checks · "
            f"{n('chains_received')} option chains received · "
            f"{n('fresh_chains')} fresh chains · "
            f"{n('contract_reviews')} CE/PE contract reviews.")
    if n('contract_reviews'):
        return (base + f" Outcomes: {n('consider')} CONSIDER, "
                f"{n('watch')} WATCH, {n('avoid')} AVOID.")
    if n('directional') == 0:
        return base + " None of the selected shares passed the movement screen; this is not a scan of all contracts."
    if n('chains_received') == 0:
        return base + " NSE option chains were unavailable; no valid contract decision can be made."
    if n('fresh_chains') == 0:
        return base + " Exchange responses did not contain verified fresh quotes."
    if n('expiry_matches') == 0:
        return base + " No supported contracts were found with a verified 8–65 day expiry."
    return base + " No contract passed the required strike, expiry and lot-size checks."


def option_http_note(stats: dict) -> str:
    """Show status counters but never secrets, HTTP URLs or raw responses."""
    codes = stats.get("http_status_counts") or {}
    if not codes:
        return ""
    parts = [f"{str(code)}: {int(count)}" for code, count in sorted(codes.items(), key=lambda p: str(p[0]))]
    return "NSE option-chain request results (HTTP status / request error): " + ", ".join(parts)
