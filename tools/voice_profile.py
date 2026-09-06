"""Manage household voice profiles.

Usage:
    venv/bin/python tools/voice_profile.py                 # status of all profiles
    venv/bin/python tools/voice_profile.py enroll <name>   # open enrollment
    venv/bin/python tools/voice_profile.py enroll <name> en # same, English script (EN mode)
    venv/bin/python tools/voice_profile.py cancel          # cancel pending enrollment
    venv/bin/python tools/voice_profile.py reset <name>    # delete a profile

Enrollment: after `enroll <name>`, have that person chat with Olympia alone
(wake word first: "Olympia, ..."). Their profile completes after 8 utterances;
watch data/merlin.log for "inscription <name> N/8". No restart needed.
`enroll` prints a guided script (ENROLL_SCRIPT): 8 French sentences, one
acoustic condition each — vary distance/volume/prosody, not the words.
`enroll <name> en` pushes the English script (ENROLL_SCRIPT_EN) — for the
English top-up measured on 2026-09-06 (docs/DECISIONS.md): the dashboard must
be in EN mode, and short dry commands are the condition the profile misses.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import notify
from voice_guard import ENROLL_TARGET, PENDING_PATH, VOICES_DIR, _normed_mean

# The gate is text-independent: what the profile needs is ACOUSTIC diversity
# (distance, volume, prosody, room), not specific words. Each sentence just
# has to clear the enrollment filters (>= 1.2 s, >= 3 words) so all 8 count.
# A single-condition session builds a brittle profile (see DECISIONS.md
# 2026-08-14, the "marées" false reject) — hence one condition per line.
ENROLL_SCRIPT = """\
Script d'inscription — seul(e) dans la pièce, une phrase à la fois, attendre
la réponse de Olympia. Redire « Olympia » si plus de ~10 s se sont écoulées
depuis sa réponse. Le contenu importe peu : ce sont les CONDITIONS qui
comptent (distance, volume, intonation).

 1. À 1 m du téléphone, voix normale :
    « Olympia, est-ce que tu m'entends bien ? »
 2. Voix normale, phrase longue :
    « Olympia, raconte-moi ce que tu sais faire dans la maison. »
 3. À 2–3 mètres du téléphone :
    « Olympia, quel temps va-t-il faire demain ? »
 4. Voix douce, comme si quelqu'un dormait à côté :
    « Olympia, parle moins fort, il est tard. »
 5. Question, intonation montante :
    « Olympia, tu crois qu'il va pleuvoir ce week-end ? »
 6. Sur un ton d'ordre :
    « Olympia, donne-moi une idée de repas pour ce soir. »
 7. Depuis l'endroit où on lui parle le plus souvent (cuisine) :
    « Qu'est-ce qu'on pourrait préparer avec des courgettes ? »
 8. En bougeant, dos au téléphone :
    « Rappelle-moi de sortir les poubelles demain matin. »
"""


ENROLL_SCRIPT_EN = """\
Enrolment script (English) — alone in the room, dashboard in EN mode, one
sentence at a time, wait for Olympia's answer. Say "Olympia" again if more
than ~10 s have passed since her reply. The words matter little: the
CONDITIONS do (distance, volume, intonation). Short commands count double —
they are what the profile misses in English.

 1. 1 m from the phone, normal voice:
    "Olympia, can you hear me well?"
 2. Normal voice, long sentence:
    "Olympia, tell me what you can do in the house."
 3. 2–3 metres from the phone:
    "Olympia, what will the weather be like tomorrow?"
 4. Soft voice, as if someone were sleeping nearby:
    "Olympia, speak more quietly, it's late."
 5. Question, rising intonation:
    "Olympia, do you think it will rain this weekend?"
 6. Command tone, short and dry:
    "Olympia, turn on the kitchen light."
 7. Another short command, from where you usually talk to her (kitchen):
    "Olympia, set a timer for ten minutes."
 8. Moving around, back to the phone:
    "Olympia, remind me to take out the bins tomorrow morning."
"""


def _push_script(name: str, kind: str, script: str = ENROLL_SCRIPT):
    # Push the script to the phone (Telegram, iMessage fallback) so the
    # person can read it while moving around the room. Best-effort.
    result = notify.send(f"Olympia — {kind} ouverte pour {name}.\n\n{script}")
    print(f"(script envoyé sur le téléphone : {result})")


def status():
    profiles = sorted(VOICES_DIR.glob("*.npz"))
    if not profiles:
        print("no profiles yet — the first person to talk enrolls as the owner")
    for path in profiles:
        embeddings = np.load(path)["embeddings"]
        n = len(embeddings)
        state = "ACTIVE" if n >= ENROLL_TARGET else f"learning ({n}/{ENROLL_TARGET})"
        line = f"{path.stem}: {n} embeddings — {state}"
        if n >= 2:
            sims = embeddings @ _normed_mean(list(embeddings))
            line += f" | consistency min={sims.min():.2f} mean={sims.mean():.2f}"
            if sims.min() < 0.35:
                line += "  ⚠ poor match inside profile — consider reset"
        print(line)
    if PENDING_PATH.exists():
        # Marker: "name [target [enrolled-so-far]]" (see voice_guard.py).
        parts = PENDING_PATH.read_text().split()
        detail = ""
        if len(parts) > 1:
            detail = f" (top-up, {parts[2] if len(parts) > 2 else 0} enrolled)"
        print(f"enrollment OPEN for '{parts[0]}'{detail} — "
              "that person should chat with Olympia alone now")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        status()
    elif cmd == "enroll":
        if len(sys.argv) < 3:
            sys.exit("usage: voice_profile.py enroll <name>")
        name = sys.argv[2].strip().lower()
        english = len(sys.argv) > 3 and sys.argv[3].strip().lower() == "en"
        script = ENROLL_SCRIPT_EN if english else ENROLL_SCRIPT
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        existing = VOICES_DIR / f"{name}.npz"
        if existing.exists():
            count = len(np.load(existing)["embeddings"])
            if count >= ENROLL_TARGET:
                # Top-up: add diversity to a complete profile (far from the
                # phone, soft voice, another room…).
                target = count + 8
                PENDING_PATH.write_text(f"{name} {target}", encoding="utf-8")
                print(f"top-up open for '{name}' ({count} -> {target}). Chat with "
                      "Olympia ALONE, varying conditions — reuse the script below, "
                      "favoring the conditions the profile misses (distance, soft "
                      "voice, another room).\n")
                print(script)
                _push_script(name, "inscription (top-up)", script)
                return
        PENDING_PATH.write_text(name, encoding="utf-8")
        print(f"enrollment open for '{name}'. Have them chat with Olympia alone "
              f"(start with the wake word). Completes after {ENROLL_TARGET} utterances.\n")
        print(script)
        _push_script(name, "inscription", script)
    elif cmd == "cancel":
        PENDING_PATH.unlink(missing_ok=True)
        print("pending enrollment cancelled")
    elif cmd == "reset":
        if len(sys.argv) < 3:
            sys.exit("usage: voice_profile.py reset <name>")
        target = VOICES_DIR / f"{sys.argv[2].strip().lower()}.npz"
        if target.exists():
            target.unlink()
            print(f"deleted {target}")
        else:
            print(f"no profile named '{sys.argv[2]}'")
    else:
        sys.exit(f"unknown command '{cmd}' (status|enroll|cancel|reset)")


if __name__ == "__main__":
    main()
