"""Offline tests for transcript_store: speaker column + old-schema migration.

Run: venv/bin/python tools/test_transcript_store.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcript_store import TranscriptStore


def test_speaker_column():
    with tempfile.TemporaryDirectory() as tmp:
        store = TranscriptStore(Path(tmp) / "t.db")
        store.append("s1", "user", "Merlin quelle heure est-il", speaker="fred")
        store.append("s1", "assistant", "Il est midi.")
        store.append("s1", "user", "[filtré: voix inconnue] blabla")
        rows = store._conn.execute("SELECT role, content, speaker FROM turns ORDER BY id").fetchall()
        assert rows[0] == ("user", "Merlin quelle heure est-il", "fred"), rows[0]
        assert rows[1][2] is None and rows[2][2] is None
        print("ok: speaker stored, NULL by default")


def test_migration_from_old_schema():
    """A pre-2026-08-18 db (no speaker column) gains it without losing rows."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            """CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, ts TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO turns (session_id, ts, role, content) VALUES ('s0', '2026-08-01T10:00:00', 'user', 'ancien tour')"
        )
        conn.commit()
        conn.close()

        store = TranscriptStore(db)
        store.append("s1", "user", "nouveau tour", speaker="fred")
        rows = store._conn.execute("SELECT content, speaker FROM turns ORDER BY id").fetchall()
        assert rows == [("ancien tour", None), ("nouveau tour", "fred")], rows
        print("ok: old schema migrated in place")


def test_lang_column():
    """Session language stored per turn; legacy callers leave it NULL."""
    with tempfile.TemporaryDirectory() as tmp:
        store = TranscriptStore(Path(tmp) / "t.db")
        store.append("s1", "user", "what time is it", speaker="fred", lang="en")
        store.append("s1", "assistant", "It is noon.", lang="en")
        store.append("s2", "user", "quelle heure", speaker="fred")
        rows = store._conn.execute("SELECT content, lang FROM turns ORDER BY id").fetchall()
        assert rows == [("what time is it", "en"), ("It is noon.", "en"), ("quelle heure", None)], rows
        print("ok: lang column")


if __name__ == "__main__":
    test_speaker_column()
    test_migration_from_old_schema()
    test_lang_column()
    print("all transcript_store tests passed")
