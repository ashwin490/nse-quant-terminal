# Version 10.7.1 — deployment import mismatch fix

## The failure

`ImportError` at `app.py:18`: `app.py` imports `option_payload_shape` and
`parse_expiry_date`, which are new exports of `nse_option_chain.py` in v10.7.
A partial deployment that updates `app.py` but not `nse_option_chain.py`
crashes before the Streamlit page starts.

## What this package changes

- Repackages both matching v10.7 source files together.
- Adds `verify_startup_imports.py` to fail early on a mixed-version checkout.
- Adds a unit test to ensure future releases keep both modules aligned.
- No portfolio state, database schema, model weights, stop-loss rules or trading history changed.

## Safe recovery on Windows PowerShell

1. Back up any local DuckDB data, models, and `.streamlit/secrets.toml`.
2. Extract this ZIP. Copy all files **inside** `NSE_Quant_Terminal` to the
   root of your existing `StockPredictorApp` project. In particular, replace
   **both** `app.py` and `nse_option_chain.py`. Never copy secret files into Git.
3. Run:

```powershell
python verify_startup_imports.py
python -m unittest discover -s tests -q
git status --short
git add app.py nse_option_chain.py verify_startup_imports.py UPDATE_V10_7_1.md tests/test_startup_imports.py
git diff --cached --stat
git commit -m "Fix mixed-version option scanner import on Streamlit Cloud"
git push origin HEAD
```

4. Verify the **GitHub remote** has the new `nse_option_chain.py`. In Streamlit
   Cloud, reboot/redeploy the app if it does not restart automatically.

Do not replace or reset any database or Supabase tables to fix this import.
Even after startup succeeds, CE/PE contracts may remain unavailable if the
exchange returns empty option-chain arrays; use live scan diagnostics.
