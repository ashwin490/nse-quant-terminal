"""Pre-deploy import consistency check for the NSE AI Terminal.

Run from the project root: python verify_startup_imports.py
Does not connect to NSE, Supabase, or a broker.
"""
import importlib
import sys
from pathlib import Path

REQUIRED = (
    "expiry_labels", "supported_expiries", "matching_rows",
    "nearby_strikes", "option_payload_shape", "parse_expiry_date",
    "exact_contract", "bid_ask", "quote_is_fresh",
    "underlying_value", "exchange_timestamp",
)


def main():
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        module = importlib.import_module("nse_option_chain")
    except Exception as exc:
        print(f"FAIL: nse_option_chain.py could not be imported: {type(exc).__name__}: {exc}")
        return 1
    missing = [name for name in REQUIRED if not callable(getattr(module, name, None))]
    print(f"Python: {sys.version.split()[0]}")
    print(f"nse_option_chain: {Path(module.__file__).resolve()}")
    if Path(module.__file__).resolve().parent != root:
        print("FAIL: Imported an option-chain helper from outside the project root")
        return 1
    if missing:
        print("FAIL: nse_option_chain.py is outdated; missing: " + ", ".join(missing))
        print("Copy the NEW nse_option_chain.py from the ZIP into this exact folder, then rerun.")
        return 1
    from nse_option_chain import (
        expiry_labels, supported_expiries, matching_rows, nearby_strikes,
        option_payload_shape, parse_expiry_date, exact_contract, bid_ask,
        quote_is_fresh, underlying_value, exchange_timestamp,
    )
    print("PASS: Every name imported by app.py exists in the local module")
    return 0


if __name__ == "__main__":
    sys.exit(main())
