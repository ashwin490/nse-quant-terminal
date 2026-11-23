# NSE AI Research Terminal — Dark v10 (paper only)

## Important answer: Can a stop or target be hit and then revert before a 15-minute refresh?

Yes. Earlier app versions could miss this on a share's entry day. The v10 share-audit path now fetches **completed one-minute OHLC candles**, in order, for the interval following an entry or the last verified review. If it observes a target or stop crossing in those candles, it records the paper event even if the latest market price has returned to the middle by the next dashboard refresh. **It is retrospective paper accounting, NOT a live broker stop order.**

- Targets and stops on the same one-minute candle cannot be chronologically ordered. The app assumes the stop first (conservative) and records the ambiguity in the trade's features.
- An entry-minute candle can contain prices from before the purchase. The engine skips an incomplete entry minute rather than treating its entire high/low as post-entry evidence.
- If minute candles are unavailable, delayed, have gaps or cannot be validated, the paper trade remains open with a feed warning. **The app cannot promise to detect every crossing.**
- It does not auto-close missing-price positions at an invented last known execution price. Such records keep their reserved paper allocation and need reliable data/reconciliation.
- **Options:** the current options journal has different quote limitations. Brief intraday *option premium* spikes may still be missed without an authoritative timestamped option quote feed. Do not treat its paper stops as actual placed orders.
- Paper fills assume threshold/open prices; real executions can be worse because of gaps, slippage, liquidity and fees.

## New dashboard

Three dark, chart-free tabs:

1. **✦ Predictions** — shares, CE and PE in one list with entry, target, stop-loss and a historical stock target-timing study.
2. **🧠 AI Learning** — ticker-by-ticker outcomes and descriptions of what can or cannot be concluded from each result.
3. **📒 Trade Ledger** — full open and historical shares/options, status filters, exports and history recovery.

Dark themes: **Midnight**, **Nebula**, **Deep Ocean**. Auto refresh is **15 minutes by default**, with optional 30/60-minute choices. Only works while a Streamlit browser session is active.

Timelines are backward-looking studies, not calibrated probabilities or guaranteed dates. Do not interpret them as a promise that a target will be reached.

## Upgrade without losing your history

1. Take a fresh copy of your `market_data.duckdb`, `models/` directory, and Streamlit Cloud configuration. Keep API keys outside Git.
2. Extract this ZIP and copy the **contents inside `NSE_Quant_Terminal/`** into your existing project root (where `app.py` lives).
3. **Do not overwrite** the existing database, real `.streamlit/secrets.toml`, or trained-model `.pkl` files. The ZIP contains no real secrets or trading history.
4. Verify `SUPABASE_URL`, `SUPABASE_KEY`, `AUTH_USER`, `AUTH_PASS_HASH` in Streamlit Secrets. Your baseline history has **106 share records and 21 historical option ideas**; if Cloud restores fewer, new allocations should stay paused until recovery.
5. Run the tests locally before deploying:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile app.py intraday_exit.py forecast_timeline.py learning_insights.py
```

6. Commit/push only source files, not secrets or the local DuckDB:

```powershell
git add .
git status
git commit -m "Dark three-tab research dashboard and one-minute paper exit checks"
git push origin HEAD
```

7. After Streamlit redeploys, check the sidebar's **Source and backup health**, the paper ledger counts, NSE quote data, and the logs. Verify that no new shares are added while the active share count exceeds the configured 10-position cap.

## Tests and limitations

- Source code compiles.
- Local tests: **60 tests discovered; 55 passed, 5 skipped** (DuckDB unavailable in this evaluation environment).
- Streamlit, DuckDB migration execution, Yahoo/NSE live minute feeds, Supabase Cloud and browser refresh have **not been end-to-end tested here**.
- NO system based on Yahoo/NSE web endpoints and Streamlit sessions can be guaranteed bug-free or run unattended for ten years. Unattended research requires persistent DB backups, an always-running scheduler, monitoring/alerts, provider contracts, model drift reviews and periodic maintenance.
- Historical paper trade outcomes reflect modelling assumptions; they do not prove actual realised trade performance.
