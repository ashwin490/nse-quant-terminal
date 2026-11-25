# NSE Terminal v11.1 — Windows DuckDB schema backup fix

## Why this patch exists

On Windows, `shutil.copy2(market_data.duckdb, ...)` can raise `PermissionError: [WinError 32]` when the migration's own writable DuckDB connection is still open. This was reproduced in `test_upgrades_old_text_capital_and_preserves_learning_history` on Python 3.13 / Windows.

## What changed

- `schema_migrations.ensure_schema()` now CHECKPOINTs and **closes** its own DuckDB connection before copying the pre-upgrade file.
- The database is reopened after a successful pre-upgrade backup, before running any migrations.
- If another program or process still locks the database, the backup or reopening step fails safely and the migration is not attempted.
- A failed copy removes its partial backup rather than leaving a misleading file.
- Two regression tests run even without DuckDB installed; the original full DuckDB migration integration test remains intact.

**No model, strategy, Supabase schema, journal rows, NSE quote logic, or GitHub workflow was changed in this patch.**

## Deploy safely

1. Export/verify the existing paper ledger backup and stop any *local* Streamlit app or other program using `market_data.duckdb`.
2. Extract this ZIP. Copy **both** `schema_migrations.py` and `tests/test_schema_migration_windows.py` into the equivalent paths of your existing StockPredictorApp folder. (The ZIP also includes the complete v11 app if needed.)
3. From PowerShell inside the app folder:

```powershell
python -m unittest discover -s tests -v
python nse_autonomous_worker.py --mode dry-run
```

4. Only if tests pass, commit the patch:

```powershell
git add schema_migrations.py tests/test_schema_migration_windows.py UPDATE_V11_1_WINDOWS_DUCKDB.md
git diff --cached --name-only
git commit -m "Fix Windows DuckDB migration backup file lock"
git push origin HEAD
```

If you want to commit the documentation too, copy `UPDATE_V11_1_WINDOWS_DUCKDB.md` to the project root before running these commands.

## If WinError 32 remains

Close local Streamlit, database explorers, Python notebooks and other processes holding the same `.duckdb` file. Don't delete the DB or force a migration: the app should continue to refuse upgrading without a backup. If antivirus/file sync briefly locks the file, retry after the handle is released. The complete copy + DuckDB integration check should be verified on Windows; the build environment may not have DuckDB.

## Important

This is a **Windows test/migration correctness fix**, not a guarantee of production reliability or ten-year autonomy. It does not resolve the separate live CE/PE market-data issue or the earlier mismatch between local/cloud option journal records.
