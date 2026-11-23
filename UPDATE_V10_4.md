# NSE AI Research Terminal — v10.4

## Changed

- **AI Learning:** Removed the per-stock four-stat overview (Trades recorded, Wins / losses, Average closed result, Potential training rows). Select a stock to see individual outcome and learning tiles. Each tile displays the saved result, entry indicator context, and a **testable hypothesis**, not a claim that a machine learning model has already changed. Exact trade prices, prior entry-time forecasts and dates are available in a single optional expander. No charts.
- **Predictions header:** Replaced the redundant Learning data KPI with two more immediately useful summaries: a combined **open paper positions** count (shares + CE + PE) and **open paper P/L in INR** based on last saved mark-to-market percentages. Remaining KPIs show allocated/unallocated paper capital under the shared ₹10 lakh budget.
- **Incomplete market data:** Unpriced active positions are counted as open but excluded from the reported P/L amount, which is marked **partial** rather than implying an accurate zero. The P/L is *unrealised, last-recorded, paper only*, and may be stale; it is not broker-executed profit or a live-market total.
- **History reconciliation:** Retains the v10.3 protection against a later cloud `last_checked` time silently replacing an existing local CLOSED status or its recorded exit timestamp. Potential disagreements are reported in the Trade Ledger.

## Preserve your history

Copy the source files over your existing app **without deleting** `market_data.duckdb`, `models/`, `.streamlit/secrets.toml`, or changing Supabase credentials. Those private files are intentionally not included. Data in Streamlit Community Cloud's local DuckDB may not persist: maintain Supabase and off-platform backups. The new summary does **not** modify any trade records, delete historical data, buy positions or change stop-loss logic.

## How to deploy

From your Windows PowerShell project folder, install your requirements and run the tests, then commit and push:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
git add .
git commit -m "Add per-trade AI learning cards and combined open paper PnL"
git push origin HEAD
```

Before `git add .`, check that `.streamlit/secrets.toml`, database backups and model artifacts containing private information are excluded as appropriate. Do not publish credentials.

## Validations and remaining limitations

The included tests cover escaped UI cards, open counts for shares/calls/puts, open P/L arithmetic, missing prices, cloud-history conflict policy and the prior intraday exit scenarios. The complete Streamlit site, exchange feeds and Supabase have not been exercised in this development environment. This browser-session auto-refresh is **not** an unattended 10-year service.
