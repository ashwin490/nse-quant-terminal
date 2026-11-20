"""Integration tests for safe DuckDB history imports (skip when DuckDB is absent)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_recovery import merge_history_database, count_history
from schema_migrations import ensure_schema

HAS_DUCKDB = importlib.util.find_spec('duckdb') is not None


@unittest.skipUnless(HAS_DUCKDB, 'DuckDB not installed in this runtime')
class HistoryImportTests(unittest.TestCase):
    def test_merge_preserves_existing_row_and_restores_old_learning_features(self):
        import duckdb
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            old=folder/'old.duckdb'
            new=folder/'main.duckdb'
            ensure_schema(old)
            ensure_schema(new)
            with duckdb.connect(str(old)) as con:
                con.execute("INSERT INTO trade_journal (trade_id,date_str,ticker,status,entry_price,features_json) VALUES (?,?,?,?,?,?)",
                            ['OLD1','2026-10-01','RELIANCE','WIN',200.0,'{"rsi_14":54}'])
                con.execute("INSERT INTO trade_journal (trade_id,date_str,ticker,status,entry_price,features_json) VALUES (?,?,?,?,?,?)",
                            ['DUP','2026-10-02','SBIN','LOSS',20.0,'{"rsi_14":20}'])
                con.execute("INSERT INTO daily_options_journal (date_key,option_contract,status,entry_premium) VALUES (?,?,?,?)",
                            ['2026-10-02','SBIN 2026-10 CE','WIN',10.0])
                con.execute("INSERT INTO option_watchlist (date_key,symbol,side,expiry,strike,decision) VALUES (?,?,?,?,?,?)",
                            ['2026-10-02','SBIN','CE','2026-10-27',600.0,'AVOID'])
            with duckdb.connect(str(new)) as con:
                con.execute("INSERT INTO trade_journal (trade_id,date_str,ticker,status,entry_price) VALUES (?,?,?,?,?)",
                            ['DUP','2026-10-02','SBIN','WIN',999.0])
            result = merge_history_database(old,new,folder/'backups')
            self.assertEqual(result['added']['trade_journal'],1)
            self.assertEqual(result['added']['daily_options_journal'],1)
            self.assertEqual(result['added']['option_watchlist'],1)
            self.assertTrue(Path(result['backup']).exists())
            self.assertEqual(count_history(new), {'trade_journal':2, 'daily_options_journal':1, 'option_watchlist':1})
            with duckdb.connect(str(new),read_only=True) as con:
                self.assertEqual(con.execute("SELECT entry_price FROM trade_journal WHERE trade_id='DUP'").fetchone()[0],999.0)
                self.assertEqual(con.execute("SELECT features_json FROM trade_journal WHERE trade_id='OLD1'").fetchone()[0],'{"rsi_14":54}')
            again = merge_history_database(old,new,folder/'backups')
            self.assertEqual(again['added']['trade_journal'],0)

    def test_rejects_unrelated_source_without_erasing_target(self):
        import duckdb
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            target=folder/'target.duckdb'
            source=folder/'unrelated.duckdb'
            ensure_schema(target)
            with duckdb.connect(str(source)) as con:
                con.execute("CREATE TABLE unrelated (id INTEGER)")
            with self.assertRaisesRegex(ValueError,'No supported'):
                merge_history_database(source,target,folder/'backups')
            self.assertEqual(count_history(target)['trade_journal'],0)

if __name__=='__main__': unittest.main()
