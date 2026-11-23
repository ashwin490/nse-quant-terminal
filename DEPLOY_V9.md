# NSE Quant Research Terminal — v9 scan transparency update

This is an **incremental update** on the colourful v8 dashboard: the three colour themes remain, with **no charts**. The existing `market_data.duckdb`, trained `.pkl` models, Supabase history, authentication credentials and paper trades are not included in the ZIP and **must not be replaced**.

## What the 23 November tab captures tell us

- **106 stock journal rows:** 18 still open and 88 recorded as finished.
- **Preservation baseline:** startup now requires at least the previously observed **106 share rows and 21 option rows** before it can record further paper allocations. This is a minimum-count check, not a proof that every record is correct. If your Streamlit Secrets still set `HISTORY_EXPECTED_MIN_SHARES = 20`, update that to `106`, and set `HISTORY_EXPECTED_MIN_OPTIONS = 21`.
- **21 saved option paper ideas:** separate from 0 newly saved `option_watchlist` contract reviews. The latter does **not** imply earlier option trades were lost.
- **₹438,490 allocated to shares; no active option allocation:** ₹561,510 is **unallocated** research budget, not instantly available for new share entries because the 10-share cap has already been exceeded by existing positions.
- **2,419 NSE EQ symbols listed:** not 2,419 verified ML recommendations. Only a small technical shortlist is shown.
- **35 of 218 F&O underlyings selected:** this is a rotating batch. The previous app did not clearly distinguish "selected for checking" from "option chain received and fresh contract reviewed."
- **Corporate filings module unavailable:** the stock score should not be described as a fully verified research decision. This update makes that limitation explicit.
- **88 completed outcomes:** does not establish a model's forward accuracy. Retraining still requires at least 100 *eligible* labelled records, plus the existing champion/challenger validation. Historical rows are preserved.

## Improvements

1. **Options scan diagnostics:** counts symbols selected, usable price histories, directional candidates, option-chain responses, fresh chains, expiry matches, and CE/PE reviews. The UI reports response status counts (e.g., 403, 429, timeout) without exposing cookies or credentials. A nonresponsive feed no longer looks like a comprehensive scan with no opportunities.
2. **Overview refresh:** after the Stock or Options tab completes a scan, rerun the page once so Overview displays the latest candidate cards rather than a stale blank. Auto-refresh still requires an open browser session; it is not a scheduled trading service.
3. **Preserve BOTH historical journals:** missing shares or missing saved options now block new paper allocations; neither dataset is reset or reconstructed. The count floors may be overridden in Streamlit Secrets only after explicitly accepting a fresh start.
4. **Paper-buy transparency:** a CONSIDER research recommendation is not necessarily a paper purchase. With 18 open positions and a 10-position cap, new stock paper purchases remain paused. Prior positions are not erased to create slots.
5. **Clearer capital label:** `Unallocated paper capital` avoids implying that all unused funds may be deployed immediately.
6. **Historical options clarity:** saved option ideas and newly created per-contract research reviews are different datasets. The history panel explains this when `option_watchlist` is empty.
7. **Model caveats:** limited historical outcomes do not validate percentage forecasts. Corporate-filing checks that are unavailable are disclosed rather than represented as having passed.

## Install (PowerShell in the repository root)

1. Back up `market_data.duckdb`, `models/`, and your real Streamlit secrets locally; keep your original Supabase project. Do **not** commit secrets or database backups to GitHub.
2. Extract this ZIP, then copy the **contents of** `NSE_Quant_Simple/` into your existing repository root (not an additional nested folder). Preserve existing database, models, and real `.streamlit/secrets.toml`.
3. Run:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

git status
git add app.py scan_health.py dashboard_theme.py dashboard_insights.py history_integrity.py history_recovery.py schema_migrations.py nse_lot_parser.py option_universe_health.py quant_guardrails.py health_check.py requirements.txt README.md DEPLOY_V9.md tests/ .streamlit/config.toml
git commit -m "Clarify stock and options scan diagnostics"
git push origin HEAD
```

4. After the cloud redeploy, verify that History & learning still has at least 106 stock records and 21 saved option ideas. If counts are lower, stop automatic paper buying and restore your original Supabase/backup history.
5. During an open NSE session, use **Calls & puts → Analyse options**. Inspect **Why did the options scan return no ideas?**. If all option-chain calls show 403/429/timeout, the data source is failing; **no** `CONSIDER` option signal should be fabricated from theoretical premiums. Verify feeds separately.

## Validation and limits

The code compiles and the unit test suite passes in this workspace; DuckDB integration tests may be skipped if DuckDB is unavailable. Live NSE endpoints, Supabase, saved model files and Streamlit browser UI are **not** available for full integration testing here. This is paper research only, with no real broker orders. It is not a ten-year unattended background service.
