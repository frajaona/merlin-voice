"""Banc Kev « est-ce adressé à Olympia ? » sur transcripts.db (lead parqué, 2026-10-21).

Kev (github.com/jaredpalmer/kev) est une famille de « modèles de décision » :
on fournit un texte + une question oui/non, il renvoie une probabilité
calibrée, sans génération (0.8B et 4B tournent sur MLX). Ce banc mesure
s'il sépare, sur nos vrais tours, la parole adressée à l'assistant du
bavardage ambiant — le seul trou textuel du gate (parole latérale de
l'activateur dans la fenêtre de suivi). Résultats et verdict dans
`docs/DECISIONS.md` 2026-10-21.

Prérequis (hors repo, jetable) :
    git clone https://github.com/jaredpalmer/kev.git && cd kev
    uv sync --extra serve
    uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009
Puis, bot arrêté ou non (lecture seule de la base) :
    venv/bin/python tools/bench_kev_addressed.py [--port 8009] [--rejected 200]

Groupes : `accepted` = tours vocaux acceptés par le gate (hors `[clavier]`),
`rejected_sample` = échantillon de tours `[filtré: hors attention …]` d'au
moins deux mots (bavardage sans mot d'éveil = négatifs approximatifs).
Chaque tour est scoré avec la dernière réponse de l'assistant de la même
session (< 120 s) en contexte. Affiche la séparation, l'effet de seuils, et
les tours acceptés les moins « adressés » (à lire à la main : c'est le juge).
"""
import argparse
import json
import re
import sqlite3
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "transcripts.db"
WAKE_RE = re.compile(r"olymp|merlin", re.I)
INSTR = ("The text is a transcript of something a person said out loud at home, where a voice assistant "
         "named Olympia (formerly Merlin) is listening. Is the person talking TO the assistant "
         "(a request, question, answer or remark meant for it), rather than to another person in the room "
         "or to themselves?")
CRITERIA = {"true": "Said to the assistant",
            "false": "Said to someone else, to themselves, or not speech directed at the assistant"}


def load_turns(n_rejected: int, seed: int = 1):
    import random
    db = sqlite3.connect(DB)
    rows = db.execute("select id, session_id, ts, role, content, speaker from turns "
                      "order by session_id, ts, id").fetchall()
    out, last_asst = [], {}
    for id_, sess, ts, role, content, speaker in rows:
        if role == "assistant":
            last_asst[sess] = (ts, content)
            continue
        if content.startswith("[clavier"):
            continue
        filt, text = None, content
        m = re.match(r"\[filtré: ([^\]]*)\] ?(.*)", content, re.S)
        if m:
            filt, text = m.group(1), m.group(2)
        prev = last_asst.get(sess)
        ctx = None
        if prev:
            try:
                if (datetime.fromisoformat(ts) - datetime.fromisoformat(prev[0])).total_seconds() < 120:
                    ctx = prev[1]
            except ValueError:
                pass
        out.append(dict(id=id_, ts=ts, text=text.strip(), speaker=speaker, filtered=filt, prev_assistant=ctx))
    acc = [r for r in out if r["filtered"] is None and r["text"]]
    rej = [r for r in out if r["filtered"] and r["filtered"].startswith("hors attention") and len(r["text"].split()) >= 2]
    random.seed(seed)
    return acc, random.sample(rej, min(n_rejected, len(rej)))


def ask(port: int, rec: dict):
    state = (f"Assistant just said: {rec['prev_assistant'][:400]}\n\nPerson now says: {rec['text']}"
             if rec.get("prev_assistant") else f"Person says: {rec['text']}")
    body = json.dumps({"state": state, "model": "kev-latest",
                       "questions": {"addressed": {"type": "noul", "instructions": INSTR, "criteria": CRITERIA}}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/systemone", data=body,
                                 headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    return res["answers"]["addressed"]["noul"], res.get("latency_ms"), (time.perf_counter() - t0) * 1000


def quantiles(ps):
    xs = sorted(ps)
    n = len(xs)
    return f"n={n} p10={xs[int(.1 * n)]:.2f} p50={xs[n // 2]:.2f} p90={xs[int(.9 * n)]:.2f}"


def auc(pos, neg):
    s = sum(1 if p > n else 0.5 if p == n else 0 for p in pos for n in neg)
    return s / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8009)
    ap.add_argument("--rejected", type=int, default=200)
    ap.add_argument("--out", help="JSON de sortie (scores par tour)")
    args = ap.parse_args()
    acc, rej = load_turns(args.rejected)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{args.port}/", timeout=5)
    except urllib.error.HTTPError:
        pass  # le serveur répond (404 sur / est normal)
    except OSError as exc:
        raise SystemExit(f"pas de serveur Kev sur :{args.port} ({exc}) — voir le docstring pour le lancer")
    for group in (acc, rej):
        for rec in group:
            rec["p"], rec["model_ms"], rec["wall_ms"] = ask(args.port, rec)
    lat = [r["model_ms"] for r in acc + rej if r["model_ms"] is not None]
    print(f"latence modèle (ms) : médiane {statistics.median(lat):.0f}, p90 {sorted(lat)[int(.9 * len(lat))]:.0f}, "
          f"wall médiane {statistics.median(r['wall_ms'] for r in acc + rej):.0f}")
    wake = [a for a in acc if WAKE_RE.search(a["text"])]
    nowake = [a for a in acc if not WAKE_RE.search(a["text"])]
    print("acceptés avec mot d'éveil", quantiles([a["p"] for a in wake]))
    print("acceptés sans mot d'éveil", quantiles([a["p"] for a in nowake]))
    print("hors attention           ", quantiles([a["p"] for a in rej]))
    print(f"AUC acceptés/hors-attention {auc([a['p'] for a in acc], [x['p'] for x in rej]):.2f}")
    for thr in (0.3, 0.5, 0.7):
        print(f"seuil {thr}: acceptés gardés {sum(a['p'] >= thr for a in acc)}/{len(acc)} "
              f"(sans éveil {sum(a['p'] >= thr for a in nowake)}/{len(nowake)}), "
              f"hors-attention laissés passer {sum(x['p'] >= thr for x in rej)}/{len(rej)}")
    print("\n--- acceptés les moins « adressés » (à lire) ---")
    for a in sorted(acc, key=lambda a: a["p"])[:40]:
        w = "W" if WAKE_RE.search(a["text"]) else " "
        print(f"{a['p']:.2f} {w} [{(a['speaker'] or '-')[:3]}] {a['text'][:80]!r}")
    print("\n--- hors attention les plus « adressés » ---")
    for a in sorted(rej, key=lambda a: -a["p"])[:15]:
        print(f"{a['p']:.2f} {a['text'][:80]!r}")
    if args.out:
        json.dump({"accepted": acc, "rejected_sample": rej}, open(args.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
