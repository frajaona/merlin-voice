"""SQLite store for conversation transcripts.

One row per spoken turn. Feeds the nightly profile summarization, the
feature-gap mining, and the STT accuracy test set. Designed so an
embeddings table can later reference turns(id) without migration.
"""
import datetime
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "transcripts.db"


class TranscriptStore:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts)")
        self._conn.commit()

    def append(self, session_id: str, role: str, content: str):
        """Synchronous insert — call via asyncio.to_thread from the pipeline."""
        content = (content or "").strip()
        if not content:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns (session_id, ts, role, content) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    role,
                    content,
                ),
            )
            self._conn.commit()
