"""Non-destructive DuckDB schema migration for the NSE paper research terminal.

This module only modifies the local DuckDB file. It does not migrate Supabase
or make Streamlit Community Cloud's ephemeral filesystem durable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

SCHEMA_VERSION = 3

# Ordered names match the original schema, so old SELECT * consumers keep working.
TABLE_COLUMNS = {
    "trade_journal": [
        ("trade_id", "VARCHAR PRIMARY KEY"),
        ("timestamp", "VARCHAR"),
        ("date_str", "VARCHAR"),
        ("ticker", "VARCHAR"),
        ("asset_type", "VARCHAR DEFAULT 'EQUITY'"),
        ("entry_price", "DOUBLE"),
        ("target_price", "DOUBLE"),
        ("stop_loss", "DOUBLE"),
        ("shares", "INTEGER"),
        ("capital_allocated", "DOUBLE"),
        ("status", "VARCHAR DEFAULT 'ACTIVE'"),
        ("latest_price", "DOUBLE"),
        ("pnl_pct", "DOUBLE DEFAULT 0.0"),
        ("exit_price", "DOUBLE DEFAULT 0.0"),
        ("exit_timestamp", "VARCHAR"),
        ("last_audited", "VARCHAR"),
        ("features_json", "VARCHAR DEFAULT '{}'"),
    ],
    "daily_options_journal": [
        ("date_key", "VARCHAR PRIMARY KEY"),
        ("timestamp", "VARCHAR"),
        ("share_name", "VARCHAR"),
        ("option_contract", "VARCHAR"),
        ("strike_price", "DOUBLE"),
        ("expiry_days", "INTEGER"),
        ("underlying_spot", "DOUBLE"),
        ("lot_size", "INTEGER"),
        ("entry_premium", "DOUBLE"),
        ("current_option_price", "DOUBLE"),
        ("target_premium", "DOUBLE"),
        ("stop_loss_premium", "DOUBLE"),
        ("total_capital", "DOUBLE"),
        ("ai_confidence", "DOUBLE"),
        ("implied_vol", "DOUBLE"),
        ("status", "VARCHAR DEFAULT 'ACTIVE'"),
        ("pnl_pct", "DOUBLE DEFAULT 0.0"),
        ("last_audited", "VARCHAR"),
    ],
    "option_watchlist": [
        ("date_key", "VARCHAR"),
        ("symbol", "VARCHAR"),
        ("side", "VARCHAR"),
        ("expiry", "VARCHAR"),
        ("strike", "DOUBLE"),
        ("spot", "DOUBLE"),
        ("bid", "DOUBLE"),
        ("ask", "DOUBLE"),
        ("lot", "INTEGER"),
        ("cost_inr", "DOUBLE"),
        ("decision", "VARCHAR"),
        ("reason", "VARCHAR"),
        ("breakeven_spot", "DOUBLE"),
        ("spread_pct", "DOUBLE"),
        ("iv_pct", "DOUBLE"),
        ("real_vol_pct", "DOUBLE"),
        ("ret_5d_pct", "DOUBLE"),
        ("checked_at", "VARCHAR"),
    ],
    "system_metadata": [("meta_key", "VARCHAR PRIMARY KEY"), ("meta_val", "VARCHAR")],
}

WATCHLIST_PK = "PRIMARY KEY (date_key, symbol, side, expiry, strike)"
NUMERIC_COLUMNS = {
    table: {name: spec.split()[0] for name, spec in defs if spec.startswith(("DOUBLE", "INTEGER"))}
    for table, defs in TABLE_COLUMNS.items()
}


class SchemaMigrationError(RuntimeError):
    """Raise rather than pretending a partially upgraded database is usable."""


def _tables(con) -> set[str]:
    return {str(row[0]).lower() for row in con.execute("SHOW TABLES").fetchall()}


def _columns(con, table: str) -> dict[str, str]:
    # Table names are taken exclusively from constants above.
    return {str(row[1]).lower(): str(row[2]).upper()
            for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()}


def _is_numeric_type(data_type: str) -> bool:
    return any(data_type.startswith(t) for t in
               ("DOUBLE", "FLOAT", "REAL", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "DECIMAL", "UBIGINT", "UINTEGER"))


def _safe_add_definition(name: str, spec: str) -> str:
    # An existing table may have different primary keys. Avoid introducing a
    # constraint via ADD COLUMN; the original data is never rebuilt or dropped.
    return spec.replace(" PRIMARY KEY", "") if name in ("trade_id", "date_key", "meta_key") else spec


def _needs_change(con) -> bool:
    present = _tables(con)
    if not set(TABLE_COLUMNS).issubset(present) or "schema_migrations" not in present:
        return True
    for table, defs in TABLE_COLUMNS.items():
        actual = _columns(con, table)
        if any(name not in actual for name, _ in defs):
            return True
        if any(not _is_numeric_type(actual[name]) for name in NUMERIC_COLUMNS[table]):
            return True
    try:
        row = con.execute("SELECT meta_val FROM system_metadata WHERE meta_key='schema_version' LIMIT 1").fetchone()
        return not row or str(row[0]) != str(SCHEMA_VERSION)
    except Exception:
        return True


def _restore_old_column(con, table: str, new_col: str, old_cols: dict[str, str], aliases: tuple[str, ...]):
    for old in aliases:
        if old.lower() in old_cols:
            con.execute(f'''UPDATE {table} SET "{new_col}" = TRY_CAST("{old}" AS DOUBLE)
                         WHERE ("{new_col}" IS NULL OR "{new_col}" = 0)
                           AND TRY_CAST("{old}" AS DOUBLE) > 0''')
            return


def _backfill(con, original: dict[str, set[str]]) -> None:
    """Backfill only missing values; never rewrite populated trading or training data."""
    share_cols = _columns(con, "trade_journal")
    if "shares" in share_cols:
        _restore_old_column(con, "trade_journal", "shares", share_cols, ("shares_qty", "quantity", "qty"))
    if "entry_price" in share_cols:
        _restore_old_column(con, "trade_journal", "entry_price", share_cols, ("buy_price", "purchase_price"))
    # Prefer the actual quantity × entry price. Legacy 'position_gbp' is a
    # misnamed remote field used by an older deployment, not a conversion.
    con.execute("""UPDATE trade_journal SET capital_allocated =
        TRY_CAST(shares AS DOUBLE) * TRY_CAST(entry_price AS DOUBLE)
        WHERE (capital_allocated IS NULL OR capital_allocated <= 0)
          AND TRY_CAST(shares AS DOUBLE) > 0 AND TRY_CAST(entry_price AS DOUBLE) > 0""")
    _restore_old_column(con, "trade_journal", "capital_allocated", share_cols,
                        ("position_inr", "allocated_inr", "position_gbp"))
    if "predicted_date" in share_cols:
        con.execute("""UPDATE trade_journal SET date_str = CAST(predicted_date AS VARCHAR)
                       WHERE (date_str IS NULL OR date_str='') AND predicted_date IS NOT NULL""")
    con.execute("""UPDATE trade_journal SET date_str = LEFT(CAST(timestamp AS VARCHAR), 10)
                   WHERE (date_str IS NULL OR date_str='') AND timestamp IS NOT NULL""")
    con.execute("""UPDATE trade_journal SET asset_type = 'EQUITY'
                   WHERE asset_type IS NULL OR asset_type = ''""")
    con.execute("""UPDATE trade_journal SET features_json='{}'
                   WHERE features_json IS NULL OR features_json=''""")
    con.execute("""UPDATE trade_journal SET latest_price=entry_price
                   WHERE latest_price IS NULL AND entry_price > 0""")
    con.execute("""UPDATE trade_journal SET pnl_pct=0 WHERE pnl_pct IS NULL""")
    # The status column of a legacy table without any status must NOT label all
    # historical rows as active. This would incorrectly reserve capital.
    for table in ("trade_journal", "daily_options_journal"):
        if "status" not in original.get(table, set()):
            con.execute(f"UPDATE {table} SET status='UNKNOWN' WHERE status IS NULL OR status='ACTIVE'")
        else:
            con.execute(f"""UPDATE {table} SET status='ACTIVE'
                            WHERE UPPER(TRIM(status)) IN ('OPEN', 'RUNNING', 'ACTIVE', '🟢 ACTIVE')""")
    con.execute("""UPDATE daily_options_journal SET total_capital =
        TRY_CAST(lot_size AS DOUBLE) * TRY_CAST(entry_premium AS DOUBLE)
        WHERE (total_capital IS NULL OR total_capital <= 0)
          AND TRY_CAST(lot_size AS DOUBLE)>0 AND TRY_CAST(entry_premium AS DOUBLE)>0""")
    opt_cols = _columns(con, "daily_options_journal")
    _restore_old_column(con, "daily_options_journal", "total_capital", opt_cols,
                        ("cost_inr", "premium_paid", "allocated_inr"))


def _unpriced_active(con) -> tuple[int, int]:
    eq = con.execute("""SELECT COUNT(*) FROM trade_journal
        WHERE status='ACTIVE' AND (capital_allocated IS NULL OR capital_allocated <= 0
                               OR entry_price IS NULL OR entry_price <= 0)""").fetchone()[0]
    opts = con.execute("""SELECT COUNT(*) FROM daily_options_journal
        WHERE status='ACTIVE' AND (total_capital IS NULL OR total_capital <= 0)""").fetchone()[0]
    return int(eq), int(opts)


def ensure_schema(db_path, backup_dir=None) -> dict:
    """Bring an existing DuckDB file up to the application's schema (idempotent).

    * Never DROP or DELETE journal rows.
    * Backup the checkpointed original before a needed upgrade.
    * Apply DDL and backfills within a transaction.
    * Fail loudly if an old numeric column contains non-numeric data.
    """
    import duckdb

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existing = db_path.is_file()
    backup_dir = Path(backup_dir) if backup_dir is not None else db_path.parent / "backups"
    con = duckdb.connect(str(db_path), read_only=False)
    info = {"version": SCHEMA_VERSION, "changed": False, "backup": None,
            "added_columns": [], "converted_columns": []}
    try:
        if not _needs_change(con):
            info["unpriced_active_shares"], info["unpriced_active_options"] = _unpriced_active(con)
            return info
        if existing:
            # Windows refuses to copy a DuckDB file while this *same process*
            # still owns a writable connection to it (WinError 32). First
            # checkpoint, then close our connection before copying the file.
            # If any other process has the DB open, the copy may still fail:
            # treat that as a hard stop, never proceed without a backup.
            con.execute("CHECKPOINT")
            con.close()
            con = None
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = backup_dir / f"market_data.before_schema_v{SCHEMA_VERSION}.{stamp}.duckdb"
            try:
                shutil.copy2(db_path, backup)
            except Exception:
                # Do not leave a partial file that could be mistaken for a
                # reliable backup. The original is unchanged at this point.
                backup.unlink(missing_ok=True)
                raise
            info["backup"] = str(backup)
            # Reacquire DuckDB's writer lock before any migration statements.
            con = duckdb.connect(str(db_path), read_only=False)
        original = {t: set(_columns(con, t)) for t in TABLE_COLUMNS if t in _tables(con)}
        con.execute("BEGIN TRANSACTION")
        try:
            for table, defs in TABLE_COLUMNS.items():
                if table not in original:
                    pieces = [f'"{name}" {spec}' for name, spec in defs]
                    if table == "option_watchlist":
                        pieces.append(WATCHLIST_PK)
                    con.execute(f"CREATE TABLE {table} ({', '.join(pieces)})")
                else:
                    existing_cols = _columns(con, table)
                    for name, spec in defs:
                        if name not in existing_cols:
                            # Avoid ADD COLUMN IF NOT EXISTS with defaults; some
                            # DuckDB versions can overwrite existing values.
                            add_spec = _safe_add_definition(name, spec)
                            if name == "status":
                                add_spec = "VARCHAR DEFAULT 'UNKNOWN'"
                            con.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {add_spec}')
                            info["added_columns"].append(f"{table}.{name}")
                # Adapt old numeric TEXT columns so SUM() is valid; refuse
                # lossy casts rather than silently converting values to NULL.
                current = _columns(con, table)
                for name, target in NUMERIC_COLUMNS[table].items():
                    if not _is_numeric_type(current[name]):
                        n_bad = con.execute(f'''SELECT COUNT(*) FROM {table}
                            WHERE "{name}" IS NOT NULL AND TRIM(CAST("{name}" AS VARCHAR)) <> ''
                              AND TRY_CAST("{name}" AS {target}) IS NULL''').fetchone()[0]
                        if n_bad:
                            raise SchemaMigrationError(f"{table}.{name}: {n_bad} values cannot convert to {target}; restore/check backup")
                        con.execute(f'''ALTER TABLE {table} ALTER COLUMN "{name}" SET DATA TYPE {target}
                                USING TRY_CAST(NULLIF(TRIM(CAST("{name}" AS VARCHAR)), '') AS {target})''')
                        info["converted_columns"].append(f"{table}.{name}")
            _backfill(con, original)
            con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations
                (version INTEGER, applied_at_utc VARCHAR, description VARCHAR)""")
            con.execute("DELETE FROM system_metadata WHERE meta_key='schema_version'")
            con.execute("INSERT INTO system_metadata (meta_key, meta_val) VALUES (?, ?)",
                        ["schema_version", str(SCHEMA_VERSION)])
            con.execute("INSERT INTO schema_migrations (version, applied_at_utc, description) VALUES (?, ?, ?)",
                        [SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(), "Add missing legacy columns and preserve existing journal/history"])
            info["unpriced_active_shares"], info["unpriced_active_options"] = _unpriced_active(con)
            con.execute("COMMIT")
            info["changed"] = True
        except Exception:
            con.execute("ROLLBACK")
            raise
        return info
    except Exception as exc:
        raise SchemaMigrationError(f"Could not upgrade DuckDB schema (original retained): {exc}") from exc
    finally:
        if con is not None:
            con.close()
