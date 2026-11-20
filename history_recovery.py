"""Non-destructive local history recovery for the NSE paper-research dashboard.

Restoration merges records by their original journal identities. It does NOT
replace a local database, overwrite existing trades, or claim cloud durability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from schema_migrations import ensure_schema, TABLE_COLUMNS

HISTORY_TABLES = ("trade_journal", "daily_options_journal", "option_watchlist")
KEY_COLUMNS = {
    "trade_journal": ("trade_id",),
    "daily_options_journal": ("date_key",),
    "option_watchlist": ("date_key", "symbol", "side", "expiry", "strike"),
}


def _backup_name(prefix: str) -> str:
    return f"{prefix}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.duckdb"


def snapshot_database(db_path, backup_dir, prefix: str = "nse_history") -> Path:
    """Create a checkpointed database copy while the caller holds its DB lock."""
    import duckdb
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError("No local DuckDB file available for backup")
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=False)
    try:
        con.execute("CHECKPOINT")
    finally:
        con.close()
    dest = backup_dir / _backup_name(prefix)
    shutil.copy2(db_path, dest)
    return dest


def count_history(db_path) -> dict:
    """Counts include open/unknown rows, which must remain visible in History."""
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        result = {}
        for table in HISTORY_TABLES:
            result[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return result
    finally:
        con.close()


def merge_history_database(source_path, target_path, backup_dir) -> dict:
    """Migrate a temporary uploaded DuckDB copy then INSERT ONLY new history IDs.

    The original user-supplied bytes remain untouched. Every target insert is
    transactional, so an error rolls back ALL changes. Existing records always win.
    """
    import duckdb
    source_path, target_path = Path(source_path), Path(target_path)
    if not source_path.is_file() or source_path.stat().st_size == 0:
        raise ValueError("The uploaded database is missing or empty")
    if source_path.resolve() == target_path.resolve():
        raise ValueError("Upload a separate old backup, not the running database")
    # A valid existing file must be supplied; never treat a bogus upload as an empty DB.
    try:
        probe = duckdb.connect(str(source_path), read_only=True)
        try:
            source_tables = {x[0].lower() for x in probe.execute("SHOW TABLES").fetchall()}
        finally:
            probe.close()
    except Exception as exc:
        raise ValueError("This file is not a readable DuckDB database") from exc
    if not source_tables.intersection(HISTORY_TABLES):
        raise ValueError("No supported paper-trade journal tables were found in this backup")

    # Old schema migrations happen ONLY to the temporary uploaded copy.
    ensure_schema(source_path, Path(backup_dir) / "staging")
    ensure_schema(target_path, backup_dir)
    backup_path = snapshot_database(target_path, backup_dir, "before_history_import")
    source = duckdb.connect(str(source_path), read_only=True)
    target = duckdb.connect(str(target_path), read_only=False)
    result = {"backup": str(backup_path), "added": {}, "already_present": {}, "source_rows": {}}
    try:
        target.execute("BEGIN TRANSACTION")
        try:
            for table in HISTORY_TABLES:
                cols = [c for c, _ in TABLE_COLUMNS[table]]
                keys = KEY_COLUMNS[table]
                col_sql = ", ".join(f'"{c}"' for c in cols)
                key_sql = ", ".join(f'"{c}"' for c in keys)
                src_rows = source.execute(f"SELECT {col_sql} FROM {table}").fetchall()
                existing = set(target.execute(f"SELECT {key_sql} FROM {table}").fetchall())
                result["source_rows"][table] = len(src_rows)
                added = 0
                ignored = 0
                for row in src_rows:
                    record = dict(zip(cols, row))
                    k = tuple(record.get(key) for key in keys)
                    if any(x is None or str(x).strip() == '' for x in k):
                        ignored += 1
                        continue
                    if k in existing:
                        ignored += 1
                        continue
                    target.execute(f"INSERT INTO {table} ({col_sql}) VALUES ({', '.join(['?'] * len(cols))})", row)
                    existing.add(k)
                    added += 1
                result["added"][table] = added
                result["already_present"][table] = ignored
            target.execute("COMMIT")
        except Exception:
            target.execute("ROLLBACK")
            raise
    finally:
        target.close()
        source.close()
    return result
