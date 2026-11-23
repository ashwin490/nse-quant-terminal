# NSE AI Research Terminal — Version 10.5

## AI Learning page redesign

The AI Learning page now **automatically shows the most recent 15 different stocks with completed paper outcomes**, newest completed exit first. No need to select a stock, an outcome filter, or a number of learning cards. Every dark tile shows the ticker, outcome, P/L, saved entry context (when present), a recorded observation, and a question the AI can test on future datasets.

- Each tile represents the **most recent completed trade** for its ticker, so the initial 15 are 15 **distinct stocks** when enough history exists.
- Click **Show 15 more stocks** to see older tickers without changing filters. The button adds 15 additional distinct stocks each time.
- Exact entry, target, stop-loss, holding-period and forecast details are available in a single optional expander after the tiles.
- Open trades are intentionally **not shown as completed learning outcomes**. They remain in the Trade Ledger and cannot be counted as confirmed wins or losses until an exit is verified.
- The model-training status is still available in its existing expander. Cards describe possible future tests; they do **not** mean the model has already retrained or identified a causal factor.

## Unchanged

The Predictions and Trade Ledger tabs, risk limits, ₹10 lakh paper budget, option scanning, order simulation, quote validation, Supabase and DuckDB history, and the three dark themes have not been modified by this update.

## Deploy

Unzip and copy the files **inside `NSE_Quant_Terminal/`** into your current `StockPredictorApp` folder. Preserve `market_data.duckdb`, `models/`, and `.streamlit/secrets.toml`. Those personal files are excluded from the ZIP. Keep verified off-platform backups; Streamlit Cloud local database files are not persistent.

From PowerShell in your project:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
git add .
git commit -m "Display latest 15 stock learning observations automatically"
git push origin HEAD
```

Never commit real Streamlit secrets or API keys.

## Validation

The packaged code passed Python compilation and 93 unit tests were executed: 88 passed; 5 DuckDB-specific integration tests were skipped because DuckDB was unavailable locally. The live NSE feed, full UI in Streamlit Cloud, and Supabase reconciliation were not exercised in this environment.
