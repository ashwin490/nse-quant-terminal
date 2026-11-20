# NSE AI research terminal — simple, graph-free edition

## What changed

- **Combined paper capital ceiling:** ₹10,00,000 (1 million INR) across stocks and **long** options.
- **Broad NSE shares:** download the official NSE *EQ-series* list, including low- and high-price stocks; apply tradability/turnover, quality, price freshness and diversification checks rather than targeting penny shares. BSE-only listings are **not** included.
- **Options:** separately evaluate bullish **CE/calls** and bearish **PE/puts** with `CONSIDER`, `WATCH`, and `AVOID`. For each option, show one-lot premium, expiry, strike, break-even price, maximum **premium** loss and an explanation. All additional fields are tucked away.
- **Authentic contracts:** current NSE F&O lot-size CSV, actual expiry dates from exchange chains and fresh two-sided prices are required for a `CONSIDER` label. The system does not invent option quotes if NSE is down.
- **Retains existing training history:** reuses the original DuckDB schema, does not drop historical legacy rows, continues the original Supabase hydration, and copies the DuckDB to a dated local backup if the original file exists.
- **Controlled model upgrades:** when at least 100 finished, labelled equity examples exist, and 25 additional new examples have accumulated, a challenger may be trained and compared with an existing champion on later-period historical observations. It is promoted only if log loss improves by at least 2% *and* AUC does not deteriorate. The initial model is never overwritten; a prior promoted champion is archived before replacement.
- **Options feedback:** retains all existing historical option paper records, but uses only **live-quoted** completed paper outcomes as directional feedback, not theoretical-price outcomes. It cautions against a direction if at least 12 previous live-quoted paper outcomes are consistently poor. This is not a calibrated option-pricing model.
- **No graphs, simpler interface:** four tabs; five share ideas and four option ideas at most are shown by default, with detailed ledgers hidden in expanders.
- **Improved security:** no default username/password, and no `?auth=` URL bypass.

## Important limitations

**This is a research/paper-trading application, NOT a live brokerage trading bot.** It **does not send buy/sell orders**, hold or transfer real funds, integrate a broker account, or offer an execution guarantee. `CONSIDER` is a research label and is not a guarantee or an instruction to trade. Historical trades are paper assumptions rather than authenticated broker fills. Derivative positions can create **physical delivery obligations at expiry** beyond the premium shown; avoid letting stock options expire in the money without specialist understanding.

**The original upload contained only `app(2).py`.** It did **not** contain `market_data.duckdb`, the saved model `models/lgbm_stock_ranker.pkl`, any existing Supabase data, or your `core/announcements.py`. Consequently, this package cannot include or reconstruct those prior assets. Do not delete them; copy the updated source files into the **existing** project folder instead, after backing it up. The scanner *pauses* if no trained model exists.

A Streamlit page only auto-refreshes **while a session is open**; this application cannot be trusted to trade, train, or monitor unattended for ten years. Real long-running service would need a separate scheduled background worker (not included), persistent database storage, authenticated broker and approved order-management integration (if desired), stale-feed alerts, periodic exchange/corporate-action reviews, backups, security updates and human oversight. Even then prediction accuracy cannot be guaranteed.

## Install without erasing old data

1. Back up the original project directory — particularly `market_data.duckdb`, `models/`, and your real Streamlit secrets.
2. Copy `app.py`, `quant_guardrails.py`, and `requirements.txt` into that **same existing** directory. Keep the previous `market_data.duckdb`, `models/lgbm_stock_ranker.pkl`, any `models/lgbm_stock_ranker_live.pkl`, and existing optional `core/announcements.py` there.
3. Install Python 3.10+ and dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Put credentials in `.streamlit/secrets.toml` or environment variables. Start from `.streamlit/secrets.toml.example`. Generate a password hash locally:

   ```bash
   python -c "import hashlib,getpass; print(hashlib.sha256(getpass.getpass('New strong password: ').encode()).hexdigest())"
   ```

   Use the generated value for `AUTH_PASS_HASH` and choose your own `AUTH_USER`. **Do not use a default password or post a secret in chat.** If you previously used the insecure URL credential, remove it from links and rotate the old password.

5. Launch the dashboard:

   ```bash
   streamlit run app.py
   ```

6. Run basic health checks and unit tests:

   ```bash
   python health_check.py
   python -m unittest discover -s tests -v
   ```

## Portfolio limits (research allocations)

| Limit | Value |
|---|---:|
| All open shares + long-option premiums | **₹10,00,000** |
| Typical single share idea | ₹1,00,000 |
| Maximum single share allocation | ₹2,00,000 |
| Maximum paper equity stop-loss exposure across shares | ₹30,000 |
| Maximum one options lot premium | ₹50,000 |
| Maximum total active long-option premiums | ₹2,00,000 |
| Maximum shares open at one time | 10 |
| Shares added in a day | Up to 10 |
| Options paper ideas | Up to 1 per market day |

Stop-loss limits are **not guaranteed** because of gap moves, slippage, exchange limits or broker execution problems. `₹10,00,000` is a simulated **allocation** ceiling; it does not measure actual broker cash/margin, unfilled orders, brokerage charges, corporate actions or unsettled exposure.

## Why a stock/option can be excluded

The scanner may say `WATCH` or `AVOID` if the official universe is unreachable, a current quote is missing, volume/open interest is thin, the option spread is too wide, implied volatility appears excessive, an expiry is too close, there is adverse corporate news, the underlying trend is unconvincing, market stress is elevated, or the ₹10 lakh ceiling would be exceeded. It is better to show no idea than fabricate a high-confidence recommendation.

## Data retention and automatic learning

- All historical `trade_journal` and `daily_options_journal` records remain in the original DuckDB database.
- New option reviews go into `option_watchlist`; rejected reviews also remain available for analysis.
- Learning is conditional on complete outcomes and quality checks. Each trained candidate is compared against the saved champion, and the original model is kept.
- Inference and learning only run when the application is executing. Maintaining a browser open is **not** a durable, supervised ten-year automation solution.
- A local daily file backup is not sufficient for disaster recovery. Use encrypted, off-site snapshots and monitor them. On some cloud platforms, local disk is ephemeral: use persistent storage or an appropriate server database.

## Official references

- [NSE individual-security derivatives information](https://www.nseindia.com/products-services/equity-derivatives-individual-securities)
- [NSE derivative contract information (permitted lot sizes)](https://www.nseindia.com/products-services/equity-derivatives-contract-information)
- [NSE option chain](https://www.nseindia.com/option-chain)

## Verification performed in this workspace

The app and the pure Python risk-control module were syntax-checked, and the guardrail unit tests were run. **A complete live Streamlit session and live NSE/Supabase integration were not tested here** because Streamlit/DuckDB installation and NSE network access were unavailable. Validate all quoted prices, file formats and execution assumptions in a production staging environment before relying on any output.


## Database schema v3: fix old DuckDB BinderException automatically

The startup migration (`schema_migrations.py`) reconciles existing tables with the
expected schema before the dashboard queries them. It creates missing columns such
as `trade_journal.capital_allocated` and `daily_options_journal.total_capital`,
converts older numeric text values only when the conversion is valid, restores
missing allocations from quantity × entry price when possible, adds a version
record in `system_metadata`, and preserves old journal rows and training features.
The process is designed to be idempotent and transactional. If an upgrade is
required, it first checkpoints and copies the existing DuckDB file into `backups/`.
Do not delete the original database or the `models/` folder.

**Fix the Streamlit Cloud deployment:** Commit **both** `app.py` and
`schema_migrations.py` along with your existing project files; a deployment with
only `app.py` cannot run. The app will run the migration at startup without a
manual SQL command. In PowerShell, from your existing app directory:

```powershell
# First, copy the updated app.py and schema_migrations.py into this folder.
git add app.py schema_migrations.py README.md tests/test_schema_migrations.py
git commit -m "Migrate DuckDB schema safely and keep AI training history"
git push origin HEAD
```

**Important:** Do not overwrite a persistent original `market_data.duckdb` with
an empty one from a ZIP. The migration cannot recover previously lost cloud-local
history. Streamlit Community Cloud does not guarantee persistence of files
written to its local disk. For long-term learning and reliable history, configure
persistent external storage and tested offsite backups; the existing Supabase
backup integration alone needs monitoring and reconciliation. Local backups in
this package are useful for an in-place upgrade but are **not** durable cloud
backups.

Check the schema version in the `🧠 Learning & history` tab or with:

```powershell
python health_check.py
python -m unittest discover -s tests -v
```

The startup reconciler protects the local DuckDB schema only. It does not alter
the remote Supabase PostgreSQL schema. A separate, reviewed server migration is
required if you change the Supabase tables.
