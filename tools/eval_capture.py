"""Toggle de la capture du jeu d'éval locuteur (data/speaker-eval/).

Usage :
  venv/bin/python tools/eval_capture.py start <nom>   # capture ON pour <nom>
  venv/bin/python tools/eval_capture.py stop
  venv/bin/python tools/eval_capture.py status

Tant que la capture est ON, chaque énoncé accepté par le STT du bot (même
canal que la prod : téléphone → WebRTC → VAD) est archivé en wav 16 k mono
+ transcript .txt sous data/speaker-eval/<nom>/. Protocole : ~20 phrases
variées AU CALME par personne (distance, volume, intonation, en mouvement),
puis `stop` — l'audio est conservé sur disque, ne pas laisser tourner.

Le jeu sert à tools/bench_speaker.py (comparaison de modèles d'embedding)
et de régression pour tout futur réglage du gate.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CAPTURE = REPO / "data" / "speaker-eval" / ".capture"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        if len(sys.argv) < 3:
            sys.exit("usage: eval_capture.py start <nom>")
        name = sys.argv[2].strip().lower()
        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE.write_text(name, encoding="utf-8")
        print(f"capture ON pour '{name}' — parle à Merlin (~20 phrases variées, "
              "au calme), puis: eval_capture.py stop")
    elif cmd == "stop":
        CAPTURE.unlink(missing_ok=True)
        print("capture OFF")
    elif cmd == "status":
        if CAPTURE.exists():
            print(f"capture ON pour '{CAPTURE.read_text().strip()}'")
        else:
            print("capture OFF")
        for d in sorted(CAPTURE.parent.glob("*/")) if CAPTURE.parent.exists() else []:
            n = len(list(d.glob("*.wav")))
            if n:
                print(f"  {d.name}: {n} énoncés")
    else:
        sys.exit(f"unknown command '{cmd}' (start|stop|status)")


if __name__ == "__main__":
    main()
