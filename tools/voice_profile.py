"""Manage household voice profiles.

Usage:
    venv/bin/python tools/voice_profile.py                 # status of all profiles
    venv/bin/python tools/voice_profile.py enroll <name>   # open enrollment
    venv/bin/python tools/voice_profile.py cancel          # cancel pending enrollment
    venv/bin/python tools/voice_profile.py reset <name>    # delete a profile

Enrollment: after `enroll <name>`, have that person chat with Merlin alone
(wake word first: "Merlin, ..."). Their profile completes after 8 utterances;
watch data/merlin.log for "inscription <name> N/8". No restart needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from voice_guard import ENROLL_TARGET, PENDING_PATH, VOICES_DIR, _normed_mean


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
        print(f"enrollment OPEN for '{PENDING_PATH.read_text().strip()}' — "
              "that person should chat with Merlin alone now")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        status()
    elif cmd == "enroll":
        if len(sys.argv) < 3:
            sys.exit("usage: voice_profile.py enroll <name>")
        name = sys.argv[2].strip().lower()
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
                      "Merlin ALONE, varying conditions: normal voice, softer "
                      "voice, 2-3 meters from the phone, another room.")
                return
        PENDING_PATH.write_text(name, encoding="utf-8")
        print(f"enrollment open for '{name}'. Have them chat with Merlin alone "
              f"(start with the wake word). Completes after {ENROLL_TARGET} utterances.")
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
