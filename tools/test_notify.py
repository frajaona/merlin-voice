"""Tests hors-ligne de notify.py (aucun envoi réel, aucun chemin réel).

Lancer : venv/bin/python tools/test_notify.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notify  # noqa: E402

for var in ("MERLIN_NOTIFY_IMESSAGE", "MERLIN_NOTIFY_TELEGRAM_TOKEN",
            "MERLIN_NOTIFY_TELEGRAM_CHAT"):
    os.environ.pop(var, None)
tmp = Path(tempfile.mkdtemp(prefix="test_notify_"))
missing_cfg = tmp / "absent.json"
cfg = tmp / "notify.json"

calls = []
tg_calls = []
tg_response = {"ok": True}


def fake_run(cmd, **kwargs):
    calls.append((cmd, kwargs))
    return types.SimpleNamespace(returncode=0)


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(req, timeout=None):
    tg_calls.append((req, timeout))
    if isinstance(tg_response, Exception):
        raise tg_response
    return _FakeResp(json.dumps(tg_response).encode("utf-8"))


real_run = notify.subprocess.run
real_urlopen = notify.urllib.request.urlopen
notify.subprocess.run = fake_run
notify.urllib.request.urlopen = fake_urlopen
try:
    # 1. Sans config ni env : disabled, et rien d'invoqué.
    assert notify.send("x", config_file=missing_cfg) == "disabled"
    assert calls == [] and tg_calls == [], "canal invoqué alors que tout est désactivé"

    # 2. iMessage seul configuré : send() retombe dessus, argv d'osascript exacts.
    cfg.write_text(json.dumps({"imessage": "+33612345678"}), encoding="utf-8")
    assert notify.send("bonjour", config_file=cfg) == "sent"
    cmd, kwargs = calls[-1]
    assert cmd == ["osascript", "-", "+33612345678", "bonjour"]
    assert kwargs.get("timeout") == 15
    assert tg_calls == []

    # 3. L'env iMessage prime sur le fichier.
    os.environ["MERLIN_NOTIFY_IMESSAGE"] = "fred@example.com"
    notify.send_imessage("re", config_file=cfg)
    assert calls[-1][0][2] == "fred@example.com"
    os.environ.pop("MERLIN_NOTIFY_IMESSAGE")

    # 4. JSON invalide ou valeur vide : disabled, pas d'exception.
    cfg.write_text("{pas du json", encoding="utf-8")
    assert notify.send("x", config_file=cfg) == "disabled"
    cfg.write_text(json.dumps({"imessage": "  "}), encoding="utf-8")
    assert notify.send("x", config_file=cfg) == "disabled"

    # 5. Échec d'osascript : "failed", jamais d'exception vers l'appelant.
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="refusé")

    notify.subprocess.run = boom
    cfg.write_text(json.dumps({"imessage": "+33612345678"}), encoding="utf-8")
    assert notify.send_imessage("x", config_file=cfg) == "failed"
    notify.subprocess.run = fake_run

    # 6. Telegram configuré : prioritaire, iMessage jamais invoqué, URL + payload.
    calls.clear()
    cfg.write_text(json.dumps({
        "imessage": "+33612345678",
        "telegram": {"token": "123:ABC", "chat_id": "42"},
    }), encoding="utf-8")
    assert notify.send("salut", config_file=cfg) == "sent"
    assert calls == [], "iMessage invoqué alors que Telegram a réussi"
    req, timeout = tg_calls[-1]
    assert req.full_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert json.loads(req.data.decode("utf-8")) == {"chat_id": "42", "text": "salut"}
    assert timeout == 15

    # 7. Les env Telegram priment sur le fichier.
    os.environ["MERLIN_NOTIFY_TELEGRAM_TOKEN"] = "999:ZZZ"
    os.environ["MERLIN_NOTIFY_TELEGRAM_CHAT"] = "7"
    notify.send_telegram("re", config_file=cfg)
    assert tg_calls[-1][0].full_url == "https://api.telegram.org/bot999:ZZZ/sendMessage"
    assert json.loads(tg_calls[-1][0].data.decode("utf-8"))["chat_id"] == "7"
    os.environ.pop("MERLIN_NOTIFY_TELEGRAM_TOKEN")
    os.environ.pop("MERLIN_NOTIFY_TELEGRAM_CHAT")

    # 8. Telegram en échec (réseau ou ok:false) : fallback iMessage, statut "sent".
    tg_response = OSError("réseau coupé")
    assert notify.send("x", config_file=cfg) == "sent"
    assert calls[-1][0][0] == "osascript"
    tg_response = {"ok": False}
    assert notify.send_telegram("x", config_file=cfg) == "failed"
    tg_response = {"ok": True}

    # 9. Telegram en échec ET iMessage en échec : "failed" (jamais d'exception).
    tg_response = OSError("réseau coupé")
    notify.subprocess.run = boom
    assert notify.send("x", config_file=cfg) == "failed"
    # Telegram en échec, iMessage non configuré : "failed" aussi.
    cfg.write_text(json.dumps({"telegram": {"token": "123:ABC", "chat_id": "42"}}),
                   encoding="utf-8")
    assert notify.send("x", config_file=cfg) == "failed"
    tg_response = {"ok": True}
    notify.subprocess.run = fake_run

    # 10. telegram partiel (token sans chat_id) : disabled, aucune requête.
    tg_calls.clear()
    cfg.write_text(json.dumps({"telegram": {"token": "123:ABC"}}), encoding="utf-8")
    assert notify.send_telegram("x", config_file=cfg) == "disabled"
    assert tg_calls == []
finally:
    notify.subprocess.run = real_run
    notify.urllib.request.urlopen = real_urlopen

print("test_notify: OK (10 cas)")
