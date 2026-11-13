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
                content TEXT NOT NULL,
                speaker TEXT,
                lang TEXT
            )"""
        )
        # Pre-2026-08-18 databases lack the speaker column.
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(turns)")}
        if "speaker" not in cols:
            self._conn.execute("ALTER TABLE turns ADD COLUMN speaker TEXT")
        # Pre-2026-09-06 databases lack the lang column (session language,
        # "fr"/"en" — lets the STT eval set be filtered per language).
        if "lang" not in cols:
            self._conn.execute("ALTER TABLE turns ADD COLUMN lang TEXT")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts)")
        # Per-turn latency (2026-10-28): one row per user→bot cycle, written by
        # bot.py from pipecat's UserBotLatencyObserver (see latency_metrics.py
        # for what each column measures; tools/latency_report.py to read it).
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS latency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('turn', 'first')),
                total_secs REAL,
                user_turn_secs REAL,
                stt_secs REAL,
                llm_ttfb_secs REAL,
                llm_runs INTEGER,
                tts_ttfb_secs REAL,
                tools_secs REAL,
                tools TEXT,
                detail TEXT,
                lang TEXT
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_latency_ts ON latency(ts)")
        self._conn.commit()

    def append(self, session_id: str, role: str, content: str, speaker: str | None = None,
               lang: str | None = None):
        """Synchronous insert — call via asyncio.to_thread from the pipeline.

        speaker: enrolled profile name the gate attributed the turn to, or
        None when unknown (assistant rows, filtered turns, typed [clavier]
        messages, gate disabled). NULL over a guessed name, always.
        lang: the session's language code ("fr"/"en"); None for legacy callers.
        """
        content = (content or "").strip()
        if not content:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns (session_id, ts, role, content, speaker, lang) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    role,
                    content,
                    speaker,
                    lang,
                ),
            )
            self._conn.commit()

    LATENCY_COLS = ("session_id", "kind", "total_secs", "user_turn_secs", "stt_secs",
                    "llm_ttfb_secs", "llm_runs", "tts_ttfb_secs", "tools_secs", "tools",
                    "detail", "lang")

    def append_latency(self, row: dict):
        """Synchronous insert of one latency record (latency_metrics.to_row)."""
        with self._lock:
            self._conn.execute(
                f"INSERT INTO latency (ts, {', '.join(self.LATENCY_COLS)}) "
                f"VALUES (?, {', '.join('?' * len(self.LATENCY_COLS))})",
                (datetime.datetime.now().isoformat(timespec="seconds"),
                 *[row.get(c) for c in self.LATENCY_COLS]),
            )
            self._conn.commit()
