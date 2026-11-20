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

## Version 4: colourful dashboard + recovery of missing history

### Appearance
- Brighter light theme, modern responsive cards, friendly colours, clear `CONSIDER` / `WATCH` / `AVOID` tags.
- Four simple tabs: **Overview**, **Stocks**, **Calls & puts**, **History & learning**.
- Searchable stock ideas and contracts; detailed figures are hidden until needed. **No price graphs.**
- The main screen only shows important capital totals and active paper positions. History is on its own tab.

### Why historical records might appear missing
Your DuckDB history file is **ignored by Git** on purpose, and a Streamlit Community Cloud instance has an **ephemeral local filesystem**. A brand-new Cloud deployment cannot automatically see a `.duckdb` file sitting only on your PC. A schema upgrade also cannot create historical records that were never available on the server. The app can only recover records actually present in an old backup or in your configured Supabase tables.

**How to recover:**
1. Open **History & learning → Recover previous data**.
2. If the old app used Supabase, set `SUPABASE_URL` and `SUPABASE_KEY` in the **Streamlit Cloud app Secrets**; click **Restore history from Supabase**. Check counts and any errors.
3. Alternatively, find your old project's **`market_data.duckdb`** or a historical `backups/*.duckdb` on your PC, and upload it using **Or choose your old DuckDB file** → **Merge my old history**. The import validates the backup, upgrades a temporary copy to the supported schema, snapshots the current DB, and inserts **only missing journal IDs**. It never intentionally overwrites an existing journal row. The displayed history then includes open, finished, and legacy `UNKNOWN` statuses (older UI hid some of these).
4. Use **Prepare downloadable DuckDB backup** and save the `.duckdb` file somewhere safe outside Streamlit Cloud. If Supabase is configured, you may also **Sync up to 200 historical rows** to its *legacy* `predictions` and `options_journal` tables (best-effort; verify result). The research review `option_watchlist` is **not** synced to Supabase by that action.
5. Keep your original `models/lgbm_stock_ranker.pkl` and any later upgraded model files separately: the history database does not contain those model binaries.

**Data cannot be recreated:** If your historical data never existed in Supabase, you have no local database backup, and the previous Streamlit Cloud instance has already been reset, no software update can restore those lost rows. Avoid uploading passwords, Supabase keys, or your local `secrets.toml` to the dashboard or Git.

### Deployment files
Copy **`app.py`, `schema_migrations.py`, `history_recovery.py`, `dashboard_theme.py`, `quant_guardrails.py`, `health_check.py`, `requirements.txt`, `.streamlit/config.toml`** and the tests from this package into your existing repo. Do **not** replace your existing `market_data.duckdb`, `models/`, or `.streamlit/secrets.toml`. Add files and push to Git, but verify `.gitignore` excludes your secrets and data.

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
git add app.py schema_migrations.py history_recovery.py dashboard_theme.py quant_guardrails.py health_check.py requirements.txt README.md .streamlit/config.toml tests/
git commit -m "Redesign NSE dashboard and add non-destructive history recovery"
git push origin HEAD
```

Some DuckDB integration tests may be skipped if DuckDB is not installed in the environment that runs tests. Verify them locally. Also test with copies of your real historical data before publishing.


## Version 6 — missing-history safety check (20 Nov 2026)

The previously displayed portfolio contained **20 active NSE share paper records**.
A later cloud deployment showed **0 active shares / 0 completed outcomes / 1 paper option**.
This is evidence of a *different or incompletely restored local data snapshot*, not proof
that those earlier records are permanently lost. **No historic trades are fabricated.**

### What this update does

- Sets `HISTORY_EXPECTED_MIN_SHARES = 20` as a **conservative baseline for this user's
  existing project**; the test is total stored share records (open and closed), not
  the number of open positions. Configure `HISTORY_EXPECTED_MIN_SHARES = "0"` in
  Streamlit Secrets **only after explicitly verifying and accepting a fresh history**.
- If fewer than 20 historical share records can be found locally, shows a prominent
  warning and **marks available capital as `Unverified`** rather than showing a
  potentially unsafe ₹10 lakh balance. New automatic share and option paper entries
  are paused until the missing records are restored. Existing paper records are
  preserved and still audited, and research scans remain accessible.
- Provides a **Retry restoring earlier shares from Supabase** action at the top of
  the dashboard when `SUPABASE_URL` and `SUPABASE_KEY` are configured.
- Differentiates Supabase fetch failures from a successfully queried but empty
  cloud project; the error details can be inspected in Streamlit logs.
- Clarifies that stock and option research ideas displayed when the NSE is closed
  are **not fresh or executable quotes**. If option quotes cannot be verified, no
  contract is promoted merely from modelled theoretical prices.

### To recover the missing 20 shares

1. In Streamlit Cloud app settings → **Secrets**, confirm that `SUPABASE_URL` and
   `SUPABASE_KEY` belong to the **original** project with your paper trading history.
   Do not share these credentials in chat or commit `.streamlit/secrets.toml`.
2. Open the dashboard, use **Retry restoring earlier shares from Supabase**, and
   inspect **History & learning → Recover previous data**. Compare the local share
   count with the remote count displayed there. If the cloud project returns zero
   shares, verify the **original** Supabase project and the `predictions` table.
3. If a copy of the original `market_data.duckdb` or a dated `.duckdb` backup
   exists on your Windows laptop, upload it in **History & learning → Recover previous
   data** and merge. Export a fresh backup after recovery.
4. If both the original cloud project and all older backups lack those records,
   the app cannot reconstruct the lost trades or training labels from screenshots.

Your Python source, GitHub repository and Streamlit Cloud deployment do **not**
automatically include local `.duckdb` files because `.gitignore` excludes them.
Keeping a Streamlit tab open does not make local storage permanent. Configure a
persistent cloud source and verify restore/backup counts before relying on automatic
learning or the ₹10 lakh allocation limit. The original model `.pkl` must also be
kept separately and backed up; past record recovery does not recreate a lost model.

New pure-Python tests: `tests/test_history_integrity.py`.
