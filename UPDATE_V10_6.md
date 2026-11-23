# Version 10.6 — NSE CE/PE scanner repair (paper only)

## What changed

The previous option scanner used only `api/option-chain-equities`, selected one ~1%-OTM strike for CE and one for PE, and could reject an entire option side merely because that one contract lacked a usable bid or ask. HTTP 200 did not prove a valid option chain had been parsed.

This update:

1. **NSE expiry discovery:** Uses exchange-reported `api/option-chain-contract-info?symbol=...` expiries and the month-specific lot sizes from the existing official F&O CSV. A different month's lot size is never borrowed.
2. **Expiry-scoped v3 API:** Tries `api/option-chain-v3?type=Equity&symbol=...&expiry=...` for an actual expiry; if response rows are missing/stale, attempts the existing legacy endpoint. If both fail, outputs no live option recommendation. This does not bypass NSE access controls or rate limits.
3. **Multiple near-market strikes:** Reviews up to seven nearby listed strikes per side, inside an 8% spot-price distance. Chooses one best-reviewed CE and one best-reviewed PE per selected underlying, preferring contracts that satisfy the unchanged conservative liquidity and risk rules. More reviewed strikes do not imply more forced CONSIDER outcomes.
4. **Exchange quote validation:** Requires a recent exchange timestamp, genuine positive uncrossed bid/ask (not LTP or Black–Scholes) and nonzero displayed depth when provided. Requires the exchange's current underlying price, and refuses a >10% disagreement between the exchange quote and cached historical close. Invalid/missing IV or realized volatility cannot become CONSIDER.
5. **Safe paper-entry re-quote:** Immediately before a newly qualified option is recorded in the paper journal, fetches the exact expiry/strike/side again and re-runs the quote, spread, trend, volatility and capital tests. Drops old shortlists (>5 minutes), premium changes >15%, unquoted and rejected contracts. The actual refreshed premium is recorded as the paper entry. No broker orders.
6. **More useful Diagnostics:** Reports actual batch attempts, exchange API endpoint status codes, v3 vs legacy rows, fresh chains, nearby strikes checked, verified bid/ask, risk reviews, best-per-side decisions and rejection reasons. An HTTP 200 alone is not a positive outcome.
7. **Progressive universe rotation:** Advances the rotating F&O scanner by symbols actually attempted, not a fixed time bucket. Stops additional requests if the exchange returns HTTP 429. A 150-second scan budget avoids monopolising a dashboard session, and incomplete batches are disclosed.
8. **Clearer filter messages:** If options were reviewed but all were WATCH or AVOID, the Predictions page tells you to choose Research signal → ALL to see them instead of simply claiming there are no options.

## Unchanged

- Dark **Predictions / AI Learning (15 stock cards) / Trade Ledger** interface.
- ₹10 lakh combined paper cap, ₹50,000 single long-option premium ceiling, ₹2 lakh total options allocation limit, stock-risk rules, and 8–65 day expiry research window.
- All original DuckDB/Supabase journal data, model files, historical positions and authentication logic.
- No synthetic premium becomes a live quote, and no live broker trades are placed.
- Default 15-minute *session-dependent* refresh; this is not a server-side scheduler or unattended market-data service.

## How to deploy

1. Back up DuckDB, Supabase tables and model files first; do not overwrite original state with the package.
2. Copy the **contents** of the ZIP's `NSE_Quant_Terminal` directory into your existing Git project root.
3. Never add `.streamlit/secrets.toml`, `.env`, `market_data.duckdb`, or actual trained `.pkl` model files to Git. The ZIP includes only example secrets and model README.
4. In the project directory, run:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
git status
git add app.py nse_option_chain.py scan_health.py quant_guardrails.py README.md UPDATE_V10_6.md tests/
git commit -m "Repair NSE CE PE option chains, quote validation and scan diagnostics"
git push origin HEAD
```

## First live verification

During the next NSE session **09:30–15:00 IST**, press `🎯 Scan CE / PE`, expand `🩺 Scan quality / full candidate list`, and examine:

- Contract-info and v3 response statuses (e.g., 200, 403, 429) separately.
- How many v3 chains included *matching expiry rows* versus how many contained fresh exchange timestamps.
- Valid bid/ask count and the reason rejected nearby strikes failed.
- CONSIDER / WATCH / AVOID for each CE/PE side using Research signal → ALL.

A result of zero CONSIDER contracts may still be correct. If NSE blocks Streamlit Cloud or its timestamps/quotes are missing, a licensed market-data provider will be necessary. This patch does not guarantee NSE connectivity or profitable signals.

## Testing limits

The code passed Python compilation and the repository's expanded unit/synthetic integration suite in the development environment, including mocked NSE v3/legacy responses and paper-entry revalidation. The runtime environment lacks `streamlit` and `duckdb`, so the five DB-dependent integration tests were skipped. The authenticated NSE feed, running Streamlit app, Supabase and broker/exchange fills were **not** tested live. A real market-hours deployment check is mandatory before trusting any CE/PE suggestion.
