"""Read-only diagnostics for NSE stock and option scans.

HTTP 200, a valid JSON payload, a fresh chain, and usable CE/PE quotes are
separate events.  None is silently reported as a successful prediction.
"""
from __future__ import annotations


def new_option_scan_health(selected: int = 0, universe: int = 0) -> dict:
    counters = (
        'underlyings_attempted', 'price_histories', 'directional',
        'contract_info_received', 'chains_received', 'v3_rows_received',
        'legacy_rows_received', 'expiry_matches', 'no_supported_expiry_lot',
        'empty_expiry_rows', 'fresh_chains', 'stale_or_untimed', 'chain_rows',
        'spot_disagreements', 'missing_underlying_spot', 'strikes_examined', 'missing_nearby_strikes',
        'missing_bid_ask', 'valid_bid_ask', 'contract_reviews',
        'guardrail_rejections', 'consider', 'watch', 'avoid',
        'time_budget_reached', 'rate_limited', 'missing_lots',
    )
    result = {name: 0 for name in counters}
    result.update(selected=max(0, int(selected)), universe=max(0, int(universe)), http_status_counts={})
    return result


def option_scan_summary(stats: dict) -> str:
    n = lambda k: max(0, int(stats.get(k, 0)))
    base = (f"{n('underlyings_attempted')}/{n('selected')} selected from {n('universe')} F&O shares actually attempted · "
            f"{n('directional')} direction-qualified · "
            f"{n('contract_info_received')} expiry-info responses · "
            f"{n('v3_rows_received')} v3 chains with matching strikes · "
            f"{n('legacy_rows_received')} legacy chains with matching strikes · "
            f"{n('fresh_chains')} fresh chains · "
            f"{n('strikes_examined')} nearby CE/PE strikes · "
            f"{n('valid_bid_ask')} valid bid/ask quotes · "
            f"{n('contract_reviews')} risk reviews. ")
    extra = ''
    if n('time_budget_reached'):
        extra += 'Scan time budget reached; remaining selected symbols were NOT checked. '
    if n('rate_limited'):
        extra += 'NSE returned HTTP 429 (rate limited); additional requests were stopped. '
    if n('contract_reviews'):
        extra += (f"Best-per-side outcomes: {n('consider')} CONSIDER · "
                  f"{n('watch')} WATCH · {n('avoid')} AVOID. ")
    elif n('directional') == 0:
        extra += 'No underlyings passed the directional movement screen. '
    elif n('chains_received') == 0:
        extra += 'No NSE chains with usable expiry rows; inspect endpoint responses. '
    elif n('fresh_chains') == 0:
        extra += 'Quote timestamps were stale/missing or not confirmed current. '
    elif n('valid_bid_ask') == 0:
        extra += 'The selected nearby strikes had no verifiable positive bid/ask. '
    elif n('contract_reviews') == 0:
        extra += 'No contract passed the minimum checks needed for a review. '
    rejects = (
        ('no_supported_expiry_lot', 'No matching allowed expiry and verified month lot'),
        ('empty_expiry_rows', 'Empty requested-expiry rows'),
        ('stale_or_untimed', 'Stale or timestamp-free chain'),
        ('spot_disagreements', 'Underlying quote/history disagreement >10%'),
        ('missing_underlying_spot', 'No current exchange underlying spot'),
        ('missing_nearby_strikes', 'No nearby CE/PE strike'),
        ('missing_bid_ask', 'Missing/crossed bid-ask or empty displayed depth'),
        ('guardrail_rejections', 'Failed liquidity/cost/other risk guardrails'),
    )
    active = [f'{label}: {n(key)}' for key, label in rejects if n(key)]
    if active:
        extra += 'Diagnostics — ' + '; '.join(active) + '.'
    return base + extra


def option_http_note(stats: dict) -> str:
    """Show endpoint/status counters but never secrets, raw URLs or responses."""
    codes = stats.get('http_status_counts') or {}
    if not codes:
        return ''
    parts = [f"{str(code)}: {int(count)}" for code, count in sorted(codes.items(), key=lambda p: str(p[0]))]
    return 'NSE option-chain requests (endpoint and HTTP/error status): ' + ', '.join(parts)
