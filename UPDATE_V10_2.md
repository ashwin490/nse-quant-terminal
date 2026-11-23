# Version 10.2 — Protected-stop and cloud-history diagnostics

## Changes

- Fixes the minute-candle checker for valid **raised/protected stop-losses** where the current stop is at or above the original entry price but below the target. Previously these were incorrectly marked as having unusable minute data, so an actual protected stop touch could be missed. The scanner still excludes incomplete entry candles, requires data coverage, assumes stop first when both levels touch the same one-minute candle, and never places real broker orders.
- Adds four regression tests for protected and invalid stops.
- Adds an **informational** warning in Trade Ledger if the last Supabase restore reported fewer option records than are present locally. This could indicate a pending sync or a stale cloud count. It neither deletes nor pushes rows automatically.
- Keeps the same three-tab dark UI, all database migrations, portfolio risk controls, and research/learning logic.

## Important limitations

- It does **not** repair missing Yahoo/NSE minute bars or NSE options chain content. If a minute interval is missing, a paper exit remains unverified. Existing stop-loss fills are historical estimates, not guaranteed market executions.
- It does **not** guarantee that a cloud option record has already synced. Confirm with Supabase and keep backups before redeployment.
- Streamlit session refreshes are **not** a background service. For unattended operation, you would need scheduled monitoring, durable storage, alerting and maintenance.
- A local DuckDB database or original model not included in this release must be preserved by the user. Do not commit real secrets to Git.

## Deploy from Windows PowerShell

Extract the `NSE_Quant_Terminal` folder, and copy its contents into your existing `StockPredictorApp` folder **without deleting** `market_data.duckdb`, `models/*.pkl`, or `.streamlit/secrets.toml`.

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
git status
git add .
git commit -m "Fix protected stop-loss reconciliation and cloud option history warnings"
git push origin HEAD
```

Check the `.gitignore` and `git status` before pushing; **never commit** your live secrets, API keys, or your production historical database.

## Test status in build environment

67 tests ran: 62 passed, five DuckDB-dependent integration tests were skipped because DuckDB was not installed. Python syntax checks passed. Live market feeds, broker fills, and Streamlit Cloud behaviour were not tested.
