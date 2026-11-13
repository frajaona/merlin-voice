"""Offline tests for latency_metrics (summary of a pipecat LatencyBreakdown)
and the `latency` table of TranscriptStore — on a tmp database, never the
real one.

Run: venv/bin/python tools/test_latency_metrics.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.observers.user_bot_latency_observer import (  # noqa: E402
    FunctionCallMetrics,
    LatencyBreakdown,
    TTFBBreakdownMetrics,
)

import latency_metrics  # noqa: E402
from transcript_store import TranscriptStore  # noqa: E402


def ttfb(processor, start, secs, model=None):
    return TTFBBreakdownMetrics(processor=processor, model=model, start_time=start,
                                duration_secs=secs)


def test_summary_plain_turn():
    b = LatencyBreakdown(
        ttfb=[
            ttfb("OpenAILLMService#0", 101.0, 0.52, "qwen"),
            ttfb("GuardedWhisperSTT#0", 100.0, 0.31),
            ttfb("KokoroTTSService#0", 102.0, 0.18),
        ],
        user_turn_start_time=99.0, user_turn_secs=0.9,
    )
    rec = latency_metrics.summarize(b, 1.42)
    assert rec["total"] == 1.42 and rec["user_turn"] == 0.9
    assert rec["stt"] == 0.31 and rec["llm"] == 0.52 and rec["tts"] == 0.18
    assert rec["llm_runs"] == 1 and rec["tools"] == [] and not rec["first_turn"]
    assert [t["processor"] for t in rec["ttfb"]][0] == "GuardedWhisperSTT#0", "sorted by time"
    line = latency_metrics.format_line(rec)
    assert line.startswith("Latency tour: total 1.42s | fin de tour 0.90s | STT 0.31s | LLM TTFB 0.52s | TTS 0.18s"), line
    msg = latency_metrics.to_message(rec)
    assert msg["event"] == "latency" and msg["total"] == 1.42 and msg["text"] == line
    print("ok: plain turn summary")


def test_summary_tool_turn_and_first_turn():
    b = LatencyBreakdown(
        ttfb=[
            ttfb("OpenAILLMService#0", 10.0, 0.4),
            ttfb("OpenAILLMService#0", 13.0, 0.6),   # second run after the tool
            ttfb("KokoroTTSService#0", 13.7, 0.2),
        ],
        function_calls=[FunctionCallMetrics(function_name="web_search", start_time=10.5,
                                            duration_secs=2.3)],
    )
    rec = latency_metrics.summarize(b, 4.1)
    assert rec["llm"] == 0.4, "first LLM run is the TTFB that matters"
    assert rec["llm_runs"] == 2 and rec["tools"] == ["web_search"]
    assert abs(rec["tools_secs"] - 2.3) < 1e-9 and rec["stt"] is None
    line = latency_metrics.format_line(rec)
    assert "LLM TTFB 0.40s (×2)" in line and "outils web_search 2.30s" in line and "STT —" in line, line

    first = latency_metrics.summarize(LatencyBreakdown(ttfb=[ttfb("OpenAILLMService#0", 1.0, 30.0)]),
                                      35.2, first_turn=True)
    assert first["first_turn"] and latency_metrics.format_line(first).startswith(
        "Latency premier tour après connexion: total 35.20s")
    row = latency_metrics.to_row(first, "s1", "fr")
    assert row["kind"] == "first" and row["llm_ttfb_secs"] == 30.0 and row["tools"] is None
    print("ok: tool turn + first turn")


def test_store_latency_table():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        store = TranscriptStore(db)
        rec = latency_metrics.summarize(LatencyBreakdown(
            ttfb=[ttfb("OpenAILLMService#0", 1.0, 0.5), ttfb("KokoroTTSService#0", 2.0, 0.1)]), 1.2)
        store.append_latency(latency_metrics.to_row(rec, "sess-1", "fr"))
        store.append_latency(latency_metrics.to_row(
            latency_metrics.summarize(LatencyBreakdown(), 20.0, first_turn=True), "sess-1", "fr"))
        rows = sqlite3.connect(db).execute(
            "select kind, total_secs, llm_ttfb_secs, tts_ttfb_secs, lang from latency order by id").fetchall()
        assert rows == [("turn", 1.2, 0.5, 0.1, "fr"), ("first", 20.0, None, None, "fr")], rows
        # Re-opening an existing database must not fail (CREATE IF NOT EXISTS).
        TranscriptStore(db)
    print("ok: latency table")


if __name__ == "__main__":
    test_summary_plain_turn()
    test_summary_tool_turn_and_first_turn()
    test_store_latency_table()
    print("all latency metrics tests passed")
