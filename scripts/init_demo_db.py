"""Create the small SQLite demo database used by the sql_query tool."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
DB_PATH = WORKSPACE / "demo.sqlite"


def init_demo_db(db_path: Path = DB_PATH) -> Path:
    """Create and seed the demo SQLite database."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS metrics;

            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE metrics (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO documents (title, source, category, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("Tool Registry Design", "demo_research_note.md", "architecture", "2026-06-16"),
                ("Trace Persistence Rules", "demo_research_note.md", "trace", "2026-06-16"),
                ("File Reader Safety", "demo_research_note.md", "safety", "2026-06-16"),
                ("Read Only SQL Policy", "demo_research_note.md", "safety", "2026-06-16"),
                ("Local RAG Foundation", "demo_research_note.md", "rag", "2026-06-16"),
                ("Phase Two Smoke Plan", "demo_research_note.md", "testing", "2026-06-16"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO metrics (name, value, unit)
            VALUES (?, ?, ?)
            """,
            [
                ("registered_tools", 5, "count"),
                ("real_handlers_day8", 2, "count"),
                ("default_file_max_chars", 8000, "chars"),
                ("max_file_chars", 20000, "chars"),
                ("default_sql_limit", 50, "rows"),
                ("max_sql_limit", 100, "rows"),
            ],
        )
        conn.commit()
    return db_path


if __name__ == "__main__":
    path = init_demo_db()
    print(f"Demo SQLite database initialized at {path}")
