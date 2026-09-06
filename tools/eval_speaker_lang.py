"""Score les clips d'éval contre les profils INSCRITS (data/voices/*.npz),
avec le scoring exact du gate (top-k, marge d'attribution).

Usage :
  venv/bin/python tools/eval_speaker_lang.py [dossier ...]
  (défaut : tous les dossiers de data/speaker-eval/ ; « fred-en » est
  comparé au profil « fred »)

Question posée (2026-09-06, mode anglais) : les profils ont été inscrits sur
du français — la gate tient-elle quand la même personne parle ANGLAIS ?
Pour chaque clip : sim contre son propre profil, meilleure sim contre les
autres profils, marge, et le verdict que rendrait le gate sur un énoncé
vérifié (≥ 1 s, ≥ 3 mots) : accepté si sim ≥ MERLIN_SPEAKER_THRESHOLD et
marge ≥ ATTRIB_MARGIN. Les clips français servent de référence (même
protocole, même canal). Lecture seule : ne touche jamais aux profils.

Une baisse en anglais se lit en FAUX REJETS (la personne n'est pas
reconnue) — jamais en fausses acceptations, sauf si un clip anglais matche
mieux le profil d'un AUTRE membre (colonne « cross », à surveiller).
"""
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from voice_guard import (  # noqa: E402
    ATTRIB_MARGIN,
    SPEAKER_THRESHOLD,
    VERIFY_MIN_SECS,
    HouseholdProfiles,
    compute_embedding,
)

EVAL_DIR = REPO / "data" / "speaker-eval"


def load_clip(path: Path) -> tuple:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, path
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0, len(pcm) / 16000.0


def person_of(label: str) -> str:
    return label[:-3] if label.endswith("-en") else label


def score_dir(d: Path, household: HouseholdProfiles) -> None:
    person = person_of(d.name)
    if person not in household.people or household.people[person].count == 0:
        print(f"\n== {d.name}: pas de profil inscrit pour '{person}', ignoré")
        return
    own = household.people[person]
    others = {n: p for n, p in household.people.items() if n != person and p.count > 0}
    clips = sorted(d.glob("*.wav"))
    if not clips:
        return
    print(f"\n== {d.name} → profil '{person}' ({own.count} embeddings), {len(clips)} clips")
    print(f"   {'clip':22} {'durée':>5} {'self':>5} {'cross':>5} {'marge':>6}  verdict  transcript")
    selfs, crosses, margins, rejects, misattrib, short = [], [], [], 0, 0, 0
    for wav in clips:
        audio, secs = load_clip(wav)
        e = compute_embedding(audio)
        s = own.similarity(e)
        cross_name, cross = None, None
        for n, p in others.items():
            v = p.similarity(e)
            if cross is None or v > cross:
                cross_name, cross = n, v
        margin = s - cross if cross is not None else None
        txt_path = wav.with_suffix(".txt")
        text = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else ""
        if secs < VERIFY_MIN_SECS:
            verdict = "court"
            short += 1
        elif s < SPEAKER_THRESHOLD:
            verdict = "REJET"
            rejects += 1
        elif margin is not None and margin < ATTRIB_MARGIN:
            verdict = "ambigu"
            rejects += 1
        else:
            verdict = "ok"
        if cross is not None and cross > s:
            misattrib += 1
            verdict += f" (>{cross_name})"
        selfs.append(s)
        if cross is not None:
            crosses.append(cross)
            margins.append(margin)
        cross_s = f"{cross:5.2f}" if cross is not None else "    -"
        margin_s = f"{margin:+6.2f}" if margin is not None else "     -"
        print(f"   {wav.name:22} {secs:5.1f} {s:5.2f} {cross_s} {margin_s}  {verdict:8} {text[:60]}")
    selfs_a = np.array(selfs)
    print(f"   -- self : min {selfs_a.min():.2f}  p10 {np.percentile(selfs_a, 10):.2f}  "
          f"médiane {np.median(selfs_a):.2f}  max {selfs_a.max():.2f}")
    if crosses:
        print(f"   -- cross max {max(crosses):.2f}  marge min {min(margins):+.2f}  "
              f"p10 {np.percentile(margins, 10):+.2f}")
    print(f"   -- verdict gate (seuil {SPEAKER_THRESHOLD}, marge {ATTRIB_MARGIN}) : "
          f"{len(clips) - rejects - short}/{len(clips) - short} acceptés, {rejects} rejets, "
          f"{short} trop courts ; clips plus proches d'un AUTRE profil : {misattrib}")


def main():
    dirs = [Path(a) for a in sys.argv[1:]] or sorted(p for p in EVAL_DIR.iterdir() if p.is_dir())
    household = HouseholdProfiles()  # lecture des profils réels — jamais modifiés ici
    for d in dirs:
        score_dir(d, household)


if __name__ == "__main__":
    main()
