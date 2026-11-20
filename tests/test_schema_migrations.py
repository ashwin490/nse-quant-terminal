"""DuckDB migration integration tests; automatically skipped without DuckDB."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schema_migrations import ensure_schema, SCHEMA_VERSION, SchemaMigrationError, TABLE_COLUMNS

DUCKDB_AVAILABLE = importlib.util.find_spec("duckdb") is not None


@unittest.skipUnless(DUCKDB_AVAILABLE, "DuckDB not installed in test environment")
class SchemaMigrationTests(unittest.TestCase):
    def test_upgrades_old_text_capital_and_preserves_learning_history(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "market_data.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute("""CREATE TABLE trade_journal (
                    trade_id VARCHAR PRIMARY KEY, date_str VARCHAR, ticker VARCHAR,
                    shares INTEGER, entry_price DOUBLE, capital_allocated VARCHAR, status VARCHAR,
                    features_json VARCHAR)""")
                con.execute("""INSERT INTO trade_journal VALUES
                    ('T1','2026-10-01','ABC',10,500,'5000','ACTIVE',
                     '{"raw_features":{"rsi_14":52}}')""")
                con.execute("""CREATE TABLE daily_options_journal (
                    date_key VARCHAR PRIMARY KEY, share_name VARCHAR, entry_premium DOUBLE,
                    lot_size INTEGER, status VARCHAR)""")
                con.execute("""INSERT INTO daily_options_journal VALUES
                    ('2026-10-01','ABC',100,10,'ACTIVE')""")
            finally:
                con.close()
            result = ensure_schema(db, Path(tmp) / "backups")
            self.assertTrue(result['changed'])
            self.assertTrue(Path(result['backup']).is_file())
            con = duckdb.connect(str(db), read_only=True)
            try:
                fields = con.execute("SELECT capital_allocated, features_json, status FROM trade_journal WHERE trade_id='T1'").fetchone()
                self.assertEqual(fields, (5000.0, '{"raw_features":{"rsi_14":52}}', 'ACTIVE'))
                self.assertEqual(con.execute('SELECT SUM(total_capital) FROM daily_options_journal').fetchone()[0], 1000.0)
                self.assertEqual(con.execute("SELECT meta_val FROM system_metadata WHERE meta_key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
                self.assertEqual(con.execute("SELECT COUNT(*) FROM trade_journal").fetchone()[0], 1)
            finally:
                con.close()
            result2 = ensure_schema(db, Path(tmp) / 'backups')
            self.assertFalse(result2['changed'])
            self.assertEqual(len(list((Path(tmp) / 'backups').glob('*.duckdb'))), 1)

    def test_unconvertible_old_data_rolls_back(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'market_data.duckdb'
            con = duckdb.connect(str(db))
            con.execute("CREATE TABLE trade_journal (trade_id VARCHAR PRIMARY KEY, capital_allocated VARCHAR)")
            con.execute("INSERT INTO trade_journal VALUES ('T1', 'not-money')")
            con.close()
            with self.assertRaises(SchemaMigrationError):
                ensure_schema(db)
            con = duckdb.connect(str(db), read_only=True)
            try:
                self.assertEqual(con.execute("SELECT capital_allocated FROM trade_journal").fetchone()[0], 'not-money')
            finally:
                con.close()

    def test_new_database_and_old_rows_kept(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'market_data.duckdb'
            result=ensure_schema(db)
            self.assertTrue(result['changed'])
            self.assertIsNone(result['backup'])
            con=duckdb.connect(str(db),read_only=True)
            try:
                for table, defs in TABLE_COLUMNS.items():
                    got={r[1] for r in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
                    self.assertTrue({name for name,_ in defs}.issubset(got))
            finally:
                con.close()

if __name__ == '__main__':
    unittest.main()
