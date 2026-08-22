"""Banc d'essai des modèles d'embedding locuteur sur NOS voix.

Usage :
  venv/bin/python tools/bench_speaker.py [--dir data/speaker-eval] [--models a,b]

Prérequis : un jeu d'éval enregistré via tools/eval_capture.py — un dossier
par personne sous data/speaker-eval/, ≥ 10 wav 16 k chacun, conditions
variées AU CALME (protocole : docs/DECISIONS.md 2026-08-22).

Pour chaque modèle candidat (téléchargé dans models/ au premier passage) :
- score = moyenne des top-3 (TOPK_SIMS, identique au gate), self en
  leave-one-out ;
- par personne : plage self, pire cross (vs le profil de chaque autre),
  pire marge self−cross et nombre de marges négatives (= mésattributions
  garanties) ;
- EER global sur les essais same/diff.

Le verdict d'un changement de modèle se lit sur la PIRE MARGE (le gate
attribue au max, la marge est ce qui protège) — pas seulement l'EER.
Après un swap : ré-inscription de tous les profils (l'espace change) et
recalibrage de MERLIN_SPEAKER_THRESHOLD / ATTRIB_MARGIN sur ces mesures.
"""
import argparse
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from voice_guard import TOPK_SIMS  # même scoring que le gate

MODELS_DIR = REPO / "models"
BASE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/")
MODELS = {
    # nom court -> nom d'asset (l'URL encode le '+')
    "campp": "wespeaker_en_voxceleb_CAM++.onnx",          # modèle ACTUEL
    "campp_lm": "wespeaker_en_voxceleb_CAM++_LM.onnx",    # même archi, finetune large-margin
    "resnet152_lm": "wespeaker_en_voxceleb_resnet152_LM.onnx",
    "resnet293_lm": "wespeaker_en_voxceleb_resnet293_LM.onnx",
    "titanet_l": "nemo_en_titanet_large.onnx",
}


def ensure_model(asset: str) -> Path:
    path = MODELS_DIR / asset
    if not path.exists():
        url = BASE + urllib.request.quote(asset)
        print(f"  téléchargement {asset}…")
        MODELS_DIR.mkdir(exist_ok=True)
        urllib.request.urlretrieve(url, path)
    return path


def load_wavs(root: Path) -> dict:
    people = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        clips = []
        for f in sorted(d.glob("*.wav")):
            with wave.open(str(f), "rb") as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 1, f
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            clips.append(pcm.astype(np.float32) / 32768.0)
        if clips:
            people[d.name] = clips
    return people


def embed_all(model_path: Path, people: dict) -> tuple:
    import sherpa_onnx

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model_path), num_threads=2)
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    out, t0, n = {}, time.monotonic(), 0
    for name, clips in people.items():
        embs = []
        for audio in clips:
            s = ex.create_stream()
            s.accept_waveform(16000, audio)
            s.input_finished()
            e = np.asarray(ex.compute(s), dtype=np.float32)
            embs.append(e / (np.linalg.norm(e) or 1.0))
            n += 1
        out[name] = np.stack(embs)
    return out, (time.monotonic() - t0) / n * 1000


def topk(mat: np.ndarray, e: np.ndarray) -> float:
    return float(np.sort(mat @ e)[-TOPK_SIMS:].mean())


def eer(same: list, diff: list) -> float:
    best = 1.0
    for t in sorted(same + diff):
        far = sum(d >= t for d in diff) / len(diff)
        frr = sum(s < t for s in same) / len(same)
        best = min(best, max(far, frr))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(REPO / "data" / "speaker-eval"))
    ap.add_argument("--models", default=",".join(MODELS))
    args = ap.parse_args()

    people = load_wavs(Path(args.dir))
    if len(people) < 2:
        sys.exit(f"besoin d'au moins 2 personnes sous {args.dir} "
                 "(tools/eval_capture.py start <nom>)")
    for name, clips in people.items():
        print(f"{name}: {len(clips)} énoncés")

    for short in args.models.split(","):
        asset = MODELS[short.strip()]
        embs, ms = embed_all(ensure_model(asset), people)
        print(f"\n== {short} ({asset}, {ms:.0f} ms/énoncé)")
        same, diff = [], []
        for name, own in embs.items():
            selfs = [topk(np.delete(own, i, axis=0), own[i]) for i in range(len(own))]
            crosses = [max(topk(other, e) for oname, other in embs.items()
                           if oname != name) for e in own]
            margins = [s - c for s, c in zip(selfs, crosses)]
            same += selfs
            diff += crosses
            neg = sum(m < 0 for m in margins)
            print(f"  {name:10s} self {min(selfs):.2f}-{max(selfs):.2f}  "
                  f"pire cross {max(crosses):.2f}  pire marge {min(margins):+.2f}  "
                  f"marges<0 : {neg}/{len(margins)}")
        print(f"  EER global : {eer(same, diff):.1%}")


if __name__ == "__main__":
    main()
