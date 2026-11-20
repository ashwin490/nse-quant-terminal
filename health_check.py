"""Independent, non-trading deployment health check.

Usage: python health_check.py
Exit 0: basic files/database healthy; exit 1: missing data/model or unreadable DB.
Schedule with your process supervisor; monitor nonzero exits. No orders are placed.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'market_data.duckdb'
MODELS = [ROOT/'models'/'lgbm_stock_ranker.pkl', ROOT/'models'/'lgbm_stock_ranker_live.pkl']

def main():
    issues=[]
    info={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"status":"OK"}
    if not DB.is_file():
        issues.append("Original DuckDB journal is missing; copy your existing market_data.duckdb")
    if not any(p.is_file() for p in MODELS):
        issues.append("Saved trained model is missing; copy models/lgbm_stock_ranker.pkl")
    if DB.is_file():
        try:
            import duckdb
            con=duckdb.connect(str(DB),read_only=True)
            try:
                from schema_migrations import SCHEMA_VERSION, TABLE_COLUMNS
                present = {str(x[0]) for x in con.execute("SHOW TABLES").fetchall()}
                for tname, defs in TABLE_COLUMNS.items():
                    if tname not in present:
                        issues.append(f"Missing table: {tname}")
                    else:
                        cols = {r[1] for r in con.execute(f"PRAGMA table_info('{tname}')").fetchall()}
                        missing = [name for name, _ in defs if name not in cols]
                        if missing: issues.append(f"{tname} lacks: {', '.join(missing)}")
                if 'system_metadata' in present:
                    v = con.execute("SELECT meta_val FROM system_metadata WHERE meta_key='schema_version' LIMIT 1").fetchone()
                    info['schema_version'] = v[0] if v else None
                    if str(info['schema_version']) != str(SCHEMA_VERSION):
                        issues.append(f"Schema version mismatch: expected {SCHEMA_VERSION}")
                for table in ('trade_journal','daily_options_journal','system_metadata','option_watchlist'):
                    info[table+'_rows']=con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                open_count=con.execute("SELECT count(*) FROM trade_journal WHERE status='ACTIVE'").fetchone()[0]
                info['open_share_ideas']=open_count
                if open_count:
                    recent=con.execute("SELECT MAX(last_audited) FROM trade_journal WHERE status='ACTIVE'").fetchone()[0]
                    info['last_open_equity_audit']=recent
            finally:
                con.close()
        except Exception as exc:
            issues.append('DuckDB could not be opened or inspected: '+str(exc)[:120])
    if issues:
        info['status']='ATTENTION'
        info['issues']=issues
    print(json.dumps(info,indent=2))
    return 1 if issues else 0

if __name__=='__main__':
    sys.exit(main())
