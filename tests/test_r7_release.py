"""Release regressions using only disposable local data."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.init_demo_db import init_demo_db
from scripts.run_offline_tests import is_loopback


class R7ReleaseTests(unittest.TestCase):
    def test_demo_restart_preserves_modified_and_extra_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite"
            init_demo_db(path)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE metrics SET value=123456 WHERE id=1")
                db.execute("CREATE TABLE preserved (content TEXT)")
                db.execute("INSERT INTO preserved VALUES ('keep')")
            before = path.read_bytes()
            init_demo_db(path)
            self.assertEqual(path.read_bytes(), before)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT value FROM metrics WHERE id=1").fetchone()[0], 123456)
                self.assertEqual(db.execute("SELECT content FROM preserved").fetchone()[0], "keep")

    def test_existing_unknown_database_is_not_reseeded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.sqlite"
            path.write_bytes(b"existing file must be inspected manually")
            before = path.read_bytes()
            init_demo_db(path)
            self.assertEqual(path.read_bytes(), before)

    def test_loopback_guard_does_not_allow_remote_or_private_addresses(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertTrue(is_loopback(host))
        for host in ("example.com", "192.168.1.1", "8.8.8.8", "localhost.example.com"):
            self.assertFalse(is_loopback(host))

    def test_docker_context_excludes_local_status_and_database_sidecars(self):
        root = Path(__file__).resolve().parents[1]
        patterns = set((root / ".dockerignore").read_text().splitlines())
        self.assertTrue({"TASK.md", "CLAUDE.md", "docs", "**/*.sqlite-wal", "**/*.sqlite-shm", "**/node_modules"} <= patterns)
