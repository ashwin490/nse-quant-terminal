"""Pure option-universe coverage checks; fail closed on broken exchange files."""
from __future__ import annotations

MIN_PLAUSIBLE_FNO_UNDERLYINGS = 50  # Not an official NSE total: only a corruption alarm.


def validate_fno_universe(lots_by_symbol: dict, equity_symbols, minimum: int = MIN_PLAUSIBLE_FNO_UNDERLYINGS) -> dict:
    """Check enough underlyings survived file parsing and NSE EQ matching.

    This is a *sanity check*, not proof of exchange completeness or availability.
    A tiny or mismatched list must never be described as 'all F&O stocks'.
    """
    eq = set(str(x).upper().replace('.NS', '') for x in (equity_symbols or []))
    lot_symbols = set(str(x).upper().strip() for x in (lots_by_symbol or {}))
    matched = sorted(eq.intersection(lot_symbols))
    count = len(matched)
    plausible = count >= max(1, int(minimum))
    if not lots_by_symbol:
        problem = "The latest NSE permitted-lot file could not be loaded or parsed."
    elif not eq:
        problem = "The NSE EQ universe is unavailable; option coverage cannot be cross-checked."
    elif not plausible:
        problem = (f"Only {count} matching F&O share(s) were found from "
                   f"{len(lot_symbols)} permitted-lot row(s). That is unusually few: "
                   "possible stale/incomplete exchange data or an incompatible file format.")
    else:
        problem = ""
    return {
        "plausible": plausible,
        "matched_symbols": matched,
        "matched": count,
        "lot_rows": len(lot_symbols),
        "minimum": max(1, int(minimum)),
        "problem": problem,
    }
