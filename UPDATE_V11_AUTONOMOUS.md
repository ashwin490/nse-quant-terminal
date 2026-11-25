# NSE AI Terminal v11 — Independent Daily Learner (paper research only)

**Read this before deployment.** v11 adds a *separate* background model and health monitor. It does not silently rewrite original share/options journals, the prior `lgbm_stock_ranker.pkl` model, the existing three-tab UI, or user credentials. It does not place broker orders.

## One-time setup (required)

1. **Export your current paper-trading ledger** via Trade Ledger → Backup and recover history. Verify your real original Supabase project still has at least 106 share and 21 option rows. Earlier snapshots showed **21 local option ideas but only 20 in Supabase**; resolve/restore the missing cloud row before treating cloud backups as complete.
2. Extract the ZIP and copy everything **inside `NSE_Quant_Terminal/`** to the root of your existing `StockPredictorApp` project. **Never overwrite your existing** `market_data.duckdb`, `models/*.pkl`, or `.streamlit/secrets.toml`. The ZIP excludes them.
3. In the **same existing Supabase project** open SQL Editor and execute `sql/001_autonomous_learning.sql` once. It creates **new** tables and two private Storage buckets. It does not edit/delete `predictions` or `options_journal`.
4. In **GitHub repository → Settings → Secrets and variables → Actions → Secrets** add `SUPABASE_URL` (same project URL) and `SUPABASE_SERVICE_ROLE_KEY` (private server-side key; not your publishable/anon key). Do not paste credentials in code, chat, or Git commits. Optional: `NSE_ALERT_WEBHOOK_URL` for HTTPS `{"text":"..."}` endpoints, and `NSE_WORKER_SYMBOLS` in **Actions Variables** as a comma-separated ticker list. Default is a 64-share diversified research basket rotating 35 per daily batch, **not all 2,420 NSE shares**.
5. Ensure the dashboard's existing `SUPABASE_URL` and `SUPABASE_KEY` can **read** the new private tables. A publishable/anon key with no authorised policy **cannot** read them; this is deliberate. Do **not** make them publicly readable for convenience. Use an appropriately secured server-side credential or user-specific authenticated RLS.
6. Locally verify:
   ```powershell
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-worker.txt
   python verify_startup_imports.py
   python -m unittest discover -s tests -v
   python nse_autonomous_worker.py --mode dry-run
   ```
7. Commit/push the new `app.py`, helpers, SQL, tests, dependencies and `.github/workflows/nse_ai_autonomous.yml` on the **default branch**. In GitHub → Actions → **NSE Independent AI Research and Monitoring** → **Run workflow**, select `dry-run` once then `daily`. Verify a `SUCCESS` record in `nse_ai_runs` in Supabase; check the Streamlit dashboard independently. Do not infer success only from a green GitHub UI if no records were written.

## What is genuinely automatic

- **EOD market-history ingestion:** The after-market GitHub job fetches up to two years of price history for a rotating subset of configurable NSE shares; it never labels the last five sessions until all five following sessions are available. Labels mark whether a +5% target or −3% stop was reached first within five *completed trading sessions*. If both appear in one daily candle the stop wins conservatively. Position costs assumed 0.28% round trip; this is an approximation, not broker fill proof.
- **Model comparison:** Dates, not random rows, define train/validation/holdout sets; 5-session horizon plus buffer are purged between partitions. A regularised logistic model and nonlinear gradient-boosted model compete on an *earlier* validation block. The winner is judged against both a constant-rate baseline and the existing champion on later unseen session dates. Brier and log-loss **must** improve materially, with enough samples, before promotion. No automatic promotion is guaranteed; rejecting a worse model is the correct result.
- **Persistent model versions:** Each accepted model is saved with a SHA-256 hash in private Supabase Storage, and its metadata and champion pointer are recorded in Supabase. The original stock model is untouched. The dashboard offers a compact, optional **Background AI daily share forecasts** section with entry/target/stop, estimate, 5-session horizon and explicit research-only labelling.
- **Independent position monitoring:** A best-effort 15-minute GitHub job checks available one-minute Yahoo prices for active cloud-paper shares and records mark/possible-touch events in separate tables. It **does not close/open positions**, backfill missing minute candles, validate actual fills, or replace the current Streamlit paper reconciler. Missing, delayed or inconsistent prices are reported, never treated as confirmed fills. Webhook alerts are optional.
- **Recovery:** A weekly *immutable, private, same-project* compressed snapshot of the original Supabase journals runs if both historical count floors are met. This is **not** an independent offsite backup or point-in-time restore. Retain separately exported backups; verify recovery periodically.
- **Health:** Worker run status and timestamp persist in `nse_ai_runs`; the dashboard flags a stale/failing daily worker. Training data is retained in `nse_ai_samples` without mutating original saved snapshots.

## Deliberate limitations (read before planning 10 years)

1. **No 10-year reliability guarantee.** GitHub schedules can be delayed or dropped; on a public repo inactivity can disable scheduled workflows after 60 days. A scheduled runner, cloud APIs, Python packages, NSE contract rules, yfinance data and Supabase service cannot be relied on for 10 years without maintenance. For serious unattended use, move the workers to a **managed job scheduler with uptime alerts**, a **licensed data feed**, external offsite backups and restore tests, and set up notifications that do not depend on the same scheduler being healthy.
2. **Original share/CE/PE paper entries and exits still run in the Streamlit app** while its session is active. The new headless monitor intentionally does **not** write to the existing ledger to avoid duplicate/conflicting exits. Full unattended execution/reconciliation remains a separate audited migration.
3. **CE/PE premium modelling is not solved by the equity learner.** The earlier NSE options chain had HTTP 200 with empty/unsupported expiry rows, zero usable quotes. Genuine options prediction requires a reliable licensed per-contract premium/Greeks/volume/history feed, documented API and independently labelled option outcomes. The v11 worker does **not** invent CE/PE values.
4. **Model probabilities are not certified/calibrated live success rates**. Time-ordered backtests reduce but do not remove selection bias, survivorship bias, provider data problems, corporate-action effects, fees or regime shifts. No guaranteed returns or target dates.
5. **Data coverage is explicitly partial.** The default rotating universe has 64 symbols and processes 35 per daily job. Configure a verified universe/feed and scale worker resources intentionally; do not claim full 2,420-share scan coverage.
6. **Security:** Only server-side service-role permissions should read/write model artifacts and private tables. `joblib` deserializes trusted, hashed objects from your own private bucket; never load models supplied by arbitrary users. Do not commit real credentials or make the bucket public.
7. **Cloud data retention and cost:** The samples table, monitor logs and Storage snapshots grow over time. Monitor billing/quotas and arrange periodic archival. The training fetch raises a hard error at its 60,000-row cap rather than silently truncating data; this is a guardrail, not a 10-year storage policy.

## Schedules

- `*/15 4-10 * * 1-5` UTC: best-effort weekday monitor (only acts when IST is between 09:30–15:30).
- `43 12 * * 1-5` UTC: weekday after-market EOD learner (~18:13 IST).
- `17 14 * * 1-5` UTC: weekday evening watchdog (alerts on a stale daily heartbeat if configured).
- GitHub Actions manual `dry-run`, `daily`, `monitor` and `health` are also provided.

**Diagnostic queries** (SQL Editor):

```sql
select run_id,kind,status,started_at,finished_at,details,error_class
from public.nse_ai_runs order by started_at desc limit 15;
select key,value from public.nse_ai_settings where key in ('champion','last_training');
select count(*) from public.nse_ai_samples;
select forecast_for,ticker,model_id,target_first_probability
from public.nse_ai_forecasts order by forecast_for desc limit 20;
select count(*) from public.predictions;
select count(*) from public.options_journal;
```

## Safe deployment commands

```powershell
python -m unittest discover -s tests -v
python verify_startup_imports.py
python nse_autonomous_worker.py --mode dry-run

git add app.py nse_ai_core.py nse_ai_dashboard.py nse_ai_store.py nse_autonomous_worker.py requirements-worker.txt sql/001_autonomous_learning.sql .github/workflows/nse_ai_autonomous.yml UPDATE_V11_AUTONOMOUS.md README.md tests/
git diff --cached --stat
git commit -m "Add independent NSE daily learning, model promotion and monitoring"
git push origin HEAD
```

The SQL Editor and GitHub Actions Secrets steps must be done manually once, and **successful scheduled data updates must be verified** before trusting it. If any new service fails to initialise, the existing v10.7.1 Streamlit trading dashboard should still load in its prior mode.
