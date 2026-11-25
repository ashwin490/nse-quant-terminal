"""Platform-independent regression checks for the Windows DuckDB backup lock.

The full old-schema migration test still exercises real DuckDB when installed.
These tests verify the lock-release ordering even on CI without DuckDB.
"""
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schema_migrations as migrations


class _FakeConnection:
    def __init__(self):
        self.closed = False
        self.sql = []

    def execute(self, sql, *args):
        if self.closed:
            raise AssertionError("Database access after connection was closed")
        self.sql.append(sql)
        return self

    def close(self):
        self.closed = True


class WindowsBackupTests(unittest.TestCase):
    def _run_mock_migration(self, source, copy_impl):
        connections = []

        def connect(*args, **kwargs):
            con = _FakeConnection()
            connections.append(con)
            return con

        with (patch.dict(sys.modules, {"duckdb": SimpleNamespace(connect=connect)}),
              patch.object(migrations, "_needs_change", return_value=True),
              patch.object(migrations, "_tables", return_value=set()),
              patch.object(migrations, "_columns", side_effect=lambda con, table: {
                  name: spec.split()[0] for name, spec in migrations.TABLE_COLUMNS[table]
              }),
              patch.object(migrations, "_backfill", return_value=None),
              patch.object(migrations, "_unpriced_active", return_value=(0, 0)),
              patch.object(migrations.shutil, "copy2", side_effect=lambda src, dst: copy_impl(src, dst, connections))):
            return migrations.ensure_schema(source, source.parent / "backups")

    def test_original_writer_closed_before_backup_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "market_data.duckdb"
            db.write_bytes(b"old paper-ledger data")
            original_copy2 = shutil.copy2

            def check_and_copy(src, dst, connections):
                self.assertEqual(len(connections), 1)
                self.assertTrue(connections[0].closed)
                self.assertIn("CHECKPOINT", connections[0].sql)
                return original_copy2(src, dst)

            result = self._run_mock_migration(db, check_and_copy)
            self.assertTrue(result["changed"])
            self.assertEqual(Path(result["backup"]).read_bytes(), db.read_bytes())
            self.assertEqual(db.read_bytes(), b"old paper-ledger data")

    def test_locked_by_other_process_aborts_without_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "market_data.duckdb"
            db.write_bytes(b"untouched paper history")

            def locked(src, dst, connections):
                self.assertTrue(connections[0].closed)
                raise PermissionError(32, "A different process still has the file open")

            with self.assertRaises(migrations.SchemaMigrationError) as ctx:
                self._run_mock_migration(db, locked)
            self.assertIn("Could not upgrade DuckDB schema", str(ctx.exception))
            self.assertEqual(db.read_bytes(), b"untouched paper history")
            self.assertEqual(list((db.parent / "backups").glob("*.duckdb")), [])


if __name__ == "__main__":
    unittest.main()
