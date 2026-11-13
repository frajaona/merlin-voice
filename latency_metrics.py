"""Per-turn latency instrumentation (TTFT by stage) — ROADMAP 19/08 lead.

Pipecat's UserBotLatencyObserver measures, for every user→bot cycle, the
time between the user's actual silence (VAD stop, corrected for stop_secs)
and the bot's first audio, and collects the per-service TTFB metrics emitted
in between (Whisper, the LLM, Kokoro) plus tool-call durations. This module
turns one of its LatencyBreakdown objects into a flat record that bot.py
logs (one INFO line per turn, grep `Latency`), stores in transcripts.db
(table `latency`, see tools/latency_report.py) and pushes to the dashboard
as a `latency` server-message.

What the numbers mean (docs/DECISIONS.md 2026-10-28):
- total      : user silence → first bot audio. The felt latency. On a tool
               turn with a spoken filler ("Je regarde ça.") this is the time
               to the FILLER; the final answer is not measured separately.
- user_turn  : silence → turn released by the aggregator (VAD stop_secs +
               STT finalisation). Included in total.
- stt        : Whisper TTFB (segment handed over → transcription out).
- llm        : TTFB of the FIRST LLM run of the cycle (prefill + first
               token). `llm_runs` counts the runs seen before first audio.
- tts        : Kokoro TTFB (first sentence in → first audio chunk out).
- tools      : names and summed duration of function calls completed
               before first audio (usually empty: fillers speak first).
- first_turn : True for the connection→first-speech measurement (the
               "~35 s on the first turn after a restart" case).
"""
from __future__ import annotations

import json
from typing import Any


def _stage(processor: str) -> str | None:
    name = processor or ""
    if "STT" in name or "Whisper" in name:
        return "stt"
    if "LLM" in name:
        return "llm"
    if "TTS" in name:
        return "tts"
    return None


def summarize(breakdown: Any, total_secs: float | None, *, first_turn: bool = False) -> dict:
    """Flatten a pipecat LatencyBreakdown into the record described above."""
    rec: dict[str, Any] = {
        "total": total_secs,
        "user_turn": getattr(breakdown, "user_turn_secs", None),
        "stt": None, "llm": None, "llm_runs": 0, "tts": None,
        "tools": [], "tools_secs": 0.0,
        "first_turn": first_turn,
        "ttfb": [],
    }
    for t in sorted(getattr(breakdown, "ttfb", []) or [], key=lambda t: t.start_time):
        stage = _stage(t.processor)
        rec["ttfb"].append({"processor": t.processor, "secs": round(t.duration_secs, 3)})
        if stage == "llm":
            rec["llm_runs"] += 1
        if stage and rec[stage] is None:  # first measurement of each stage
            rec[stage] = t.duration_secs
    for fc in getattr(breakdown, "function_calls", []) or []:
        rec["tools"].append(fc.function_name)
        rec["tools_secs"] += fc.duration_secs
    return rec


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}s"


def format_line(rec: dict) -> str:
    """One-line human summary for the log (and the dashboard)."""
    head = "premier tour après connexion" if rec["first_turn"] else "tour"
    parts = [f"total {_fmt(rec['total'])}"]
    if rec["user_turn"] is not None:
        parts.append(f"fin de tour {_fmt(rec['user_turn'])}")
    parts.append(f"STT {_fmt(rec['stt'])}")
    llm = f"LLM TTFB {_fmt(rec['llm'])}"
    if rec["llm_runs"] > 1:
        llm += f" (×{rec['llm_runs']})"
    parts.append(llm)
    parts.append(f"TTS {_fmt(rec['tts'])}")
    if rec["tools"]:
        parts.append(f"outils {','.join(rec['tools'])} {_fmt(rec['tools_secs'])}")
    return f"Latency {head}: " + " | ".join(parts)


def to_row(rec: dict, session_id: str, lang: str | None) -> dict:
    """Columns for TranscriptStore.append_latency."""
    return {
        "session_id": session_id,
        "kind": "first" if rec["first_turn"] else "turn",
        "total_secs": rec["total"],
        "user_turn_secs": rec["user_turn"],
        "stt_secs": rec["stt"],
        "llm_ttfb_secs": rec["llm"],
        "llm_runs": rec["llm_runs"],
        "tts_ttfb_secs": rec["tts"],
        "tools_secs": rec["tools_secs"] if rec["tools"] else None,
        "tools": ",".join(rec["tools"]) or None,
        "detail": json.dumps(rec["ttfb"], ensure_ascii=False),
        "lang": lang,
    }


def to_message(rec: dict) -> dict:
    """Payload of the `latency` RTVI server-message (dashboard)."""
    return {
        "event": "latency",
        "first_turn": rec["first_turn"],
        "total": rec["total"],
        "stt": rec["stt"],
        "llm": rec["llm"],
        "llm_runs": rec["llm_runs"],
        "tts": rec["tts"],
        "tools": rec["tools"],
        "tools_secs": rec["tools_secs"],
        "text": format_line(rec),
    }
