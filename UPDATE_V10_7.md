# Version 10.7 — CE/PE expiry matching and response diagnostics

**Observed blocker:** 25 Nov 2026 live dashboard scanned 35 F&O stocks,
found 29 direction-qualified, received HTTP 200 for contract info / v3 /
legacy but matched ZERO contract rows. Therefore ZERO quotes, ZERO risk reviews
and no CE/PE predictions. This cannot be resolved by relaxing risk filters.

## Changes

1. Read **both** `records.data` and `filtered.data` for matching explicit
   exchange-listed expiry rows (previously `filtered` was ignored if
   `records.data` was present for another expiry).
2. Normalize recognized unambiguous expiry labels including exchange
   `DD-MMM-YYYY` and ISO `YYYY-MM-DD`, but never invent expiry dates.
3. Try **up to two** supported exchange-listed expiries per F&O stock, with
   verified *month-specific* lot sizes, when the first lacks a fresh matching
   chain. Stop on HTTP 429; bound requests and scan duration as before.
4. Describe empty payloads, other-expiry-only rows, missing expiry on rows
   and unknown HTTP-200 JSON structures separately, with at most four
   sanitized examples. No raw quote payloads, credentials or cookies are logged.
5. Keep CE and PE independently assessed, with unchanged bid/ask, expiry,
   capital, IV, depth, spread, OI and paper-entry freshness safeguards.
6. Keep the dark three-tab layout, all historical journals and models
   unchanged. No broker trades.

## Limits

This is a reproducible parser and diagnostics fix, **not proof that live NSE
feeds now return contracts**. The development environment cannot access live
NSE endpoints, Streamlit Cloud, your Supabase or trained-model runtime.
Empty/unusable upstream responses remain an unsolved data-source issue.
Never substitute Black–Scholes prices or LTP for a missing tradable quote.
`4/5 trend conditions passed` is rule-based: no derivative-specific model has
been trained and validated yet.

## Deploy

Back up DuckDB / Supabase and protect `.streamlit/secrets.toml`, `.env`, and
real trained models. Copy the **contents of** `NSE_Quant_Terminal/` from the
ZIP into the existing project root (do not replace local DB/models/secrets).

```powershell
python -m unittest discover -s tests -v
git status
git add app.py nse_option_chain.py scan_health.py README.md UPDATE_V10_7.md tests/
git commit -m "Diagnose NSE empty option chains and retry verified expiries"
git push origin HEAD
```

Next market-hours check (09:30–15:00 IST): expand Predictions > Scan quality.
Read `Response samples` and `Diagnostics` as well as HTTP statuses. If
these show empty / unknown-shape arrays for both expiries, identify the feed
response or arrange a provider integration before expecting CE/PE signals.
