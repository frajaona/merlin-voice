"""Tests hors-ligne de workshop.run_worker : bascule de modèle agy.

Le catalogue agy change d'un appel à l'autre (22/10) : un modèle inconnu fait
sortir `agy -p` en rc=1 « invalid model selection » (ou rc=3 NOT_FOUND 404).
run_worker doit alors essayer le modèle suivant de AGY_MODELS, mais ne doit
PAS enchaîner sur un vrai échec de build (rc≠0 sans motif de catalogue).
Un faux `agy` est posé en tête du PATH ; rien n'appelle le vrai CLI.

    venv/bin/python tools/test_workshop_workers.py
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import workshop  # noqa: E402

FAKE_AGY = """#!/bin/sh
# Faux agy : journalise les --model reçus, refuse ceux listés dans FAKE_AGY_REJECT,
# plante sur ceux de FAKE_AGY_CRASH, accepte le reste.
model=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--model" ]; then model="$2"; shift; fi
  shift
done
echo "$model" >> "$FAKE_AGY_LOG"
case " $FAKE_AGY_REJECT " in *" $model "*)
  echo "error: invalid model selection (--model \\"$model\\" --effort \\"\\"): model $model is not recognized" >&2; exit 1;; esac
case " $FAKE_AGY_CRASH " in *" $model "*) echo "error: boom" >&2; exit 2;; esac
echo OK
"""


def calls(log: Path) -> list[str]:
    return log.read_text().split() if log.exists() else []


def main():
    tmp = Path(tempfile.mkdtemp(prefix="merlin-fake-agy-"))
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    agy = bin_dir / "agy"
    agy.write_text(FAKE_AGY)
    agy.chmod(agy.stat().st_mode | stat.S_IEXEC)
    log = tmp / "calls.txt"
    # PATH réduit au faux binaire (+ sh) : le vrai agy ne doit jamais être appelé,
    # surtout pas au cas 5 où le faux est supprimé.
    os.environ["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    os.environ["FAKE_AGY_LOG"] = str(log)
    workshop.AGY_MODELS[:] = ["m1", "m2", "m3"]

    # 1. Premier modèle accepté : un seul appel.
    log.unlink(missing_ok=True)
    os.environ["FAKE_AGY_REJECT"] = ""
    os.environ["FAKE_AGY_CRASH"] = ""
    assert workshop.run_worker("agy", "prompt") is True
    assert calls(log) == ["m1"], calls(log)
    print("ok: premier modèle accepté → un seul appel")

    # 2. m1 et m2 refusés par le catalogue → m3 accepté.
    log.unlink()
    os.environ["FAKE_AGY_REJECT"] = "m1 m2"
    assert workshop.run_worker("agy", "prompt") is True
    assert calls(log) == ["m1", "m2", "m3"], calls(log)
    print("ok: modèles refusés par le catalogue → bascule jusqu'au suivant accepté")

    # 3. Tous refusés → False, sans exception.
    log.unlink()
    os.environ["FAKE_AGY_REJECT"] = "m1 m2 m3"
    assert workshop.run_worker("agy", "prompt") is False
    assert calls(log) == ["m1", "m2", "m3"], calls(log)
    print("ok: tous refusés → échec propre (codex prendra le relais)")

    # 4. Vrai échec de build sur m1 (rc=2, pas un motif de catalogue) : on ne
    #    relance PAS le même build sur m2 (coût, et le résultat partiel est à jeter).
    log.unlink()
    os.environ["FAKE_AGY_REJECT"] = ""
    os.environ["FAKE_AGY_CRASH"] = "m1"
    assert workshop.run_worker("agy", "prompt") is False
    assert calls(log) == ["m1"], calls(log)
    print("ok: vrai échec de build → pas de bascule de modèle")

    # 5. CLI absente → False (FileNotFoundError absorbée), log vide.
    log.unlink()
    agy.unlink()
    assert workshop.run_worker("agy", "prompt") is False
    assert calls(log) == []
    print("ok: agy introuvable → échec propre")

    # 6. L'env AGY_MODEL reste une liste à virgules, le premier = AGY_MODEL.
    assert workshop.AGY_MODEL == "claude-sonnet-5-5-high" or workshop.AGY_MODEL == os.environ.get("AGY_MODEL", "").split(",")[0].strip()
    print(json.dumps({"AGY_MODELS_default_head": workshop.AGY_MODEL}, ensure_ascii=False))
    print("all workshop worker tests passed")


if __name__ == "__main__":
    main()
