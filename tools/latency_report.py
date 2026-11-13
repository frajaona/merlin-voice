"""Latency report from transcripts.db (table `latency`, written per turn by
bot.py since 2026-10-28 — see latency_metrics.py for what each column is).

Answers the 19/08 question « où part le temps ? » once a week of use has
accumulated: median / p90 per stage, the first turn after a connection,
and the slowest turns to look at by hand (with the user text of the turn).

Run (read-only, bot running or not):
    venv/bin/python tools/latency_report.py [--days 7] [--worst 10] [--db data/transcripts.db]
"""
import argparse
import datetime
import sqlite3
import statistics
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "transcripts.db"


def pct(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, round(p / 100 * (len(vals) - 1))))
    return vals[k]


def fmt(v):
    return "   —  " if v is None else f"{v:6.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--worst", type=int, default=10)
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = (datetime.datetime.now() - datetime.timedelta(days=args.days)).isoformat(timespec="seconds")
    try:
        rows = conn.execute("SELECT * FROM latency WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            print("Pas encore de table `latency` : le bot crée la table à son démarrage "
                  "(code du 2026-10-28) — redémarrer le bot puis faire quelques tours.")
            return
        raise
    turns = [r for r in rows if r["kind"] == "turn"]
    firsts = [r for r in rows if r["kind"] == "first"]
    print(f"{len(turns)} tours, {len(firsts)} premiers tours après connexion, depuis {since[:16]} "
          f"({len({r['session_id'] for r in rows})} sessions)")
    if not rows:
        return

    cols = [("total", "total_secs"), ("fin de tour", "user_turn_secs"), ("STT", "stt_secs"),
            ("LLM TTFB", "llm_ttfb_secs"), ("TTS", "tts_ttfb_secs"), ("outils", "tools_secs")]
    print("\nPar étape (secondes)          n   médiane     p90      max")
    for label, col in cols:
        vals = [r[col] for r in turns if r[col] is not None]
        if not vals:
            continue
        print(f"  {label:<24} {len(vals):4d}   {fmt(statistics.median(vals))}  {fmt(pct(vals, 90))}  {fmt(max(vals))}")
    tool_turns = [r for r in turns if r["tools"]]
    if tool_turns:
        print(f"  (tours avec outil avant la première parole : {len(tool_turns)})")

    # First turn of each session (the "~35 s after a restart" case of 19/08).
    # kind='first' rows (connection → first speech) only exist if the bot
    # speaks before the user does, which it never does today.
    seen, first_turns = set(), []
    for r in turns:
        if r["session_id"] not in seen:
            seen.add(r["session_id"])
            first_turns.append(r)
    for label, group in (("Premier tour de chaque session", first_turns),
                         ("Connexion → première parole", firsts)):
        vals = [r["total_secs"] for r in group if r["total_secs"] is not None]
        llm = [r["llm_ttfb_secs"] for r in group if r["llm_ttfb_secs"] is not None]
        if vals:
            print(f"\n{label} ({len(vals)}) : total médian {fmt(statistics.median(vals))} s, "
                  f"max {fmt(max(vals))} s ; LLM TTFB médian {fmt(statistics.median(llm) if llm else None)} s")

    worst = sorted(turns, key=lambda r: r["total_secs"] or 0, reverse=True)[: args.worst]
    if worst:
        print(f"\n{len(worst)} tours les plus lents (total | STT / LLM / TTS / outils) — texte du tour :")
        for r in worst:
            text = conn.execute(
                "SELECT content FROM turns WHERE session_id = ? AND role = 'user' AND ts <= ? "
                "AND content NOT LIKE '[filtré:%' ORDER BY ts DESC LIMIT 1",
                (r["session_id"], r["ts"])).fetchone()
            tools = f" / {r['tools']} {r['tools_secs']:.1f}" if r["tools"] else ""
            print(f"  {r['ts'][5:16]} {fmt(r['total_secs'])} | {fmt(r['stt_secs'])} / "
                  f"{fmt(r['llm_ttfb_secs'])}{'×' + str(r['llm_runs']) if (r['llm_runs'] or 0) > 1 else ''} / "
                  f"{fmt(r['tts_ttfb_secs'])}{tools}  « {(text['content'] if text else '?')[:70]} »")


if __name__ == "__main__":
    main()
