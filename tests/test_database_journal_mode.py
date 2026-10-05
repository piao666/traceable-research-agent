"""SQLite journal-mode configuration stays safe across host and Docker runs."""

import sqlite3

from app.database import _configure_sqlite_connection, sqlite_journal_mode


def test_sqlite_journal_mode_defaults_to_wal(monkeypatch) -> None:
    monkeypatch.delenv("SQLITE_JOURNAL_MODE", raising=False)
    assert sqlite_journal_mode() == "WAL"


def test_sqlite_journal_mode_accepts_docker_delete_mode(monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "delete")
    assert sqlite_journal_mode() == "DELETE"


def test_sqlite_journal_mode_rejects_pragma_injection(monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "WAL; DROP TABLE agent_runs")
    assert sqlite_journal_mode() == "WAL"


def test_matching_delete_mode_does_not_attempt_exclusive_switch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    path = tmp_path / "shared.sqlite"
    first = sqlite3.connect(path)
    first.execute("CREATE TABLE evidence (value TEXT)")
    first.execute("INSERT INTO evidence VALUES ('kept')")
    first.commit()
    first.execute("BEGIN")
    assert first.execute("SELECT value FROM evidence").fetchone() == ("kept",)
    second = sqlite3.connect(path, timeout=0.1)
    try:
        _configure_sqlite_connection(second, None)
        assert second.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        first.rollback()
        first.close()
        second.close()
