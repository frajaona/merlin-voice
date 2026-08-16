"""Tests hors-ligne de notify.py (aucun envoi réel, aucun chemin réel).

Lancer : venv/bin/python tools/test_notify.py
"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notify  # noqa: E402

os.environ.pop("MERLIN_NOTIFY_IMESSAGE", None)
tmp = Path(tempfile.mkdtemp(prefix="test_notify_"))
missing_cfg = tmp / "absent.json"
cfg = tmp / "notify.json"

calls = []


def fake_run(cmd, **kwargs):
    calls.append((cmd, kwargs))
    return types.SimpleNamespace(returncode=0)


real_run = notify.subprocess.run
notify.subprocess.run = fake_run
try:
    # 1. Sans config ni env : disabled, et osascript jamais invoqué.
    assert notify.send_imessage("x", config_file=missing_cfg) == "disabled"
    assert calls == [], "osascript invoqué alors que le canal est désactivé"

    # 2. Config fichier : envoi, destinataire et texte passés en argv d'osascript.
    cfg.write_text(json.dumps({"imessage": "+33612345678"}), encoding="utf-8")
    assert notify.send_imessage("bonjour", config_file=cfg) == "sent"
    cmd, kwargs = calls[-1]
    assert cmd == ["osascript", "-", "+33612345678", "bonjour"]
    assert kwargs.get("timeout") == 15

    # 3. L'env prime sur le fichier.
    os.environ["MERLIN_NOTIFY_IMESSAGE"] = "fred@example.com"
    notify.send_imessage("re", config_file=cfg)
    assert calls[-1][0][2] == "fred@example.com"
    os.environ.pop("MERLIN_NOTIFY_IMESSAGE")

    # 4. JSON invalide ou valeur vide : disabled, pas d'exception.
    cfg.write_text("{pas du json", encoding="utf-8")
    assert notify.send_imessage("x", config_file=cfg) == "disabled"
    cfg.write_text(json.dumps({"imessage": "  "}), encoding="utf-8")
    assert notify.send_imessage("x", config_file=cfg) == "disabled"

    # 5. Échec d'osascript : "failed", jamais d'exception vers l'appelant.
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="refusé")

    notify.subprocess.run = boom
    cfg.write_text(json.dumps({"imessage": "+33612345678"}), encoding="utf-8")
    assert notify.send_imessage("x", config_file=cfg) == "failed"
finally:
    notify.subprocess.run = real_run

print("test_notify: OK (5 cas)")
