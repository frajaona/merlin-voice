"""Notifications sortantes — iMessage via Messages.app (AppleScript locale).

Canal best-effort : jamais bloquant pour l'appelant, jamais d'exception.
Sortant uniquement (l'approbation reste vocale ou CLI).

Destinataire (handle iMessage : numéro « +336… » ou adresse Apple ID),
par ordre de priorité :
  1. env MERLIN_NOTIFY_IMESSAGE
  2. data/notify.json : {"imessage": "+33612345678"}
Sans destinataire configuré, send_imessage() renvoie "disabled" sans rien faire.

Premier envoi : macOS demande l'autorisation Automation (contrôler
« Messages ») au binaire appelant — à accorder une fois par contexte
(Terminal, et le process qui lance l'atelier). Test manuel :
  venv/bin/python notify.py "coucou depuis Merlin"
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
CONFIG_FILE = REPO / "data" / "notify.json"

# Le texte passe en argument du handler `on run` : aucun échappement à faire.
_APPLESCRIPT = """\
on run {targetId, msg}
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        send msg to participant targetId of svc
    end tell
end run
"""


def recipient(config_file: Path = CONFIG_FILE) -> str | None:
    env = os.getenv("MERLIN_NOTIFY_IMESSAGE", "").strip()
    if env:
        return env
    try:
        value = (json.loads(config_file.read_text(encoding="utf-8")) or {}).get("imessage", "")
        return value.strip() or None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def send_imessage(text: str, config_file: Path = CONFIG_FILE) -> str:
    """Envoie `text` au destinataire configuré.

    Retourne "sent", "disabled" (pas de destinataire) ou "failed".
    """
    to = recipient(config_file)
    if not to:
        return "disabled"
    try:
        subprocess.run(
            ["osascript", "-", to, text],
            input=_APPLESCRIPT, text=True, capture_output=True, timeout=15, check=True,
        )
        return "sent"
    except Exception:
        return "failed"


if __name__ == "__main__":
    message = " ".join(sys.argv[1:]) or "Test de notification Merlin."
    status = send_imessage(message)
    print(f"iMessage: {status}" + ("" if status == "sent" else
          " (configurer data/notify.json ou MERLIN_NOTIFY_IMESSAGE)" if status == "disabled" else
          " (voir l'autorisation Automation et le handle)"))
    sys.exit(0 if status == "sent" else 1)
