"""Toggle de la capture du jeu d'éval locuteur (data/speaker-eval/).

Usage :
  venv/bin/python tools/eval_capture.py start <nom>      # capture ON pour <nom> (script français)
  venv/bin/python tools/eval_capture.py start <nom> en   # script ANGLAIS, capturé sous <nom>-en/
  venv/bin/python tools/eval_capture.py stop
  venv/bin/python tools/eval_capture.py status

Variante anglaise (2026-09-06) : mêmes conditions, phrases anglaises simples ;
le dashboard doit être connecté en mode EN (Whisper anglais, même canal que
la prod anglaise). Les clips vont dans <nom>-en/ pour laisser intact le jeu
français de référence ; tools/eval_speaker_lang.py les score contre les
profils inscrits (en français) — c'est la mesure « la gate tient-elle quand
on parle anglais ? ».

Tant que la capture est ON, chaque énoncé accepté par le STT du bot (même
canal que la prod : téléphone → WebRTC → VAD) est archivé en wav 16 k mono
+ transcript .txt sous data/speaker-eval/<nom>/. PAS BESOIN de dire
« Olympia » : la capture est en amont du gate — Olympia restera muet sur la
plupart des phrases, c'est normal. Une personne à la fois, AU CALME, puis
`stop` — l'audio est conservé sur disque, ne pas laisser tourner.

Le jeu sert à tools/bench_speaker.py (comparaison de modèles d'embedding)
et de régression pour tout futur réglage du gate.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import notify

CAPTURE = REPO / "data" / "speaker-eval" / ".capture"

EVAL_SCRIPT = """\
Script d'éval — 20 phrases, seul(e), pièce CALME (pas de musique). Pas
besoin de dire « Olympia » ni d'attendre une réponse : tout est enregistré.
Marquer une petite pause (~1 s) entre les phrases. La CONDITION compte plus
que le texte.

À 1 m du téléphone, voix normale :
 1. Bonjour, je fais l'enregistrement de ma voix pour la maison.
 2. Le train de quinze heures quarante part du quai numéro deux.
 3. Il y a trois pommes, deux poires et un kilo de cerises dans le panier.
 4. Chaque samedi matin, on va au marché du village acheter du fromage.

Toujours à 1 m, une phrase LONGUE d'une traite :
 5. Quand les vacances arrivent, on charge la voiture très tôt le matin
    pour éviter les bouchons et on s'arrête à midi pour pique-niquer.

À 2–3 mètres, voix normale :
 6. La lumière du couloir est restée allumée toute la nuit.
 7. Est-ce que quelqu'un a vu mes clés et mon portefeuille ?
 8. Le chauffage se met en route à six heures et demie en hiver.

De l'autre bout de la pièce, un peu plus FORT :
 9. Le repas est prêt, tout le monde à table !
10. N'oubliez pas de fermer les volets avant de partir.

Voix DOUCE, comme si quelqu'un dormait à côté :
11. Il est tard, on parlera de tout ça demain matin.
12. Éteins la petite lampe quand tu montes te coucher.

Question, intonation MONTANTE :
13. Tu crois qu'il va faire beau pour la randonnée de dimanche ?
14. On invite les voisins pour l'apéritif vendredi soir ?

Ton d'ORDRE, sec et bref :
15. Allume la lumière de la cuisine.
16. Baisse le volume dans le séjour.
17. Mets un minuteur de dix minutes.

En MARCHANT dans la pièce, dos au téléphone par moments :
18. Je range les courses pendant que l'eau des pâtes chauffe.
19. Le sèche-linge fait un bruit bizarre depuis ce matin.

Assis(e), voix relâchée de fin de journée :
20. Voilà, c'est la dernière phrase, l'enregistrement est terminé.

Puis : venv/bin/python tools/eval_capture.py stop"""

EVAL_SCRIPT_EN = """\
English eval script — 20 sentences, alone, QUIET room (no music). Dashboard
in EN mode (tap EN before connecting). No need to say "Olympia" or to wait
for an answer: everything is recorded. Small pause (~1 s) between sentences.
The CONDITION matters more than the words — read naturally, your own accent.

1 m from the phone, normal voice:
 1. Hello, I am recording my voice for the house.
 2. The train at three forty leaves from platform number two.
 3. There are three apples, two pears and a kilo of cherries in the basket.
 4. Every Saturday morning we go to the village market to buy cheese.

Still at 1 m, one LONG sentence in one breath:
 5. When the holidays come, we load the car very early in the morning
    to avoid the traffic, and we stop at noon for a picnic.

At 2–3 metres, normal voice:
 6. The light in the hallway stayed on all night.
 7. Has anyone seen my keys and my wallet?
 8. The heating starts at half past six in winter.

From the other side of the room, a bit LOUDER:
 9. Dinner is ready, everybody to the table!
10. Don't forget to close the shutters before you leave.

SOFT voice, as if someone were sleeping nearby:
11. It's late, we will talk about all this tomorrow morning.
12. Turn off the small lamp when you go up to bed.

Question, RISING intonation:
13. Do you think the weather will be nice for the hike on Sunday?
14. Shall we invite the neighbours for a drink on Friday evening?

COMMAND tone, short and dry:
15. Turn on the kitchen light.
16. Turn down the volume in the living room.
17. Set a timer for ten minutes.

WALKING around the room, back to the phone at times:
18. I am putting away the shopping while the pasta water heats up.
19. The dryer has been making a strange noise since this morning.

Sitting, relaxed end-of-day voice:
20. That's it, this is the last sentence, the recording is finished.

Then: venv/bin/python tools/eval_capture.py stop"""


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        if len(sys.argv) < 3:
            sys.exit("usage: eval_capture.py start <nom>")
        name = sys.argv[2].strip().lower()
        english = len(sys.argv) > 3 and sys.argv[3].strip().lower() == "en"
        label = f"{name}-en" if english else name
        script = EVAL_SCRIPT_EN if english else EVAL_SCRIPT
        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE.write_text(label, encoding="utf-8")
        print(f"capture ON pour '{label}'\n")
        print(script)
        # Push sur le téléphone (Telegram, best-effort) pour lire en bougeant.
        result = notify.send(f"Olympia — éval voix pour {label}.\n\n{script}")
        print(f"\n(script envoyé sur le téléphone : {result})")
    elif cmd == "stop":
        CAPTURE.unlink(missing_ok=True)
        print("capture OFF")
        for d in sorted(CAPTURE.parent.glob("*/")) if CAPTURE.parent.exists() else []:
            n = len(list(d.glob("*.wav")))
            if n:
                print(f"  {d.name}: {n} énoncés")
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
