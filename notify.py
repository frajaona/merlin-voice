"""Notifications sortantes — Telegram (prioritaire) puis iMessage (fallback).

Canal best-effort : jamais bloquant pour l'appelant, jamais d'exception.
Sortant uniquement (l'approbation reste vocale ou CLI).

Pourquoi Telegram d'abord (2026-08-21) : les iMessages envoyés depuis son
propre Apple ID vers soi-même arrivent SANS bannière de notification (iOS
les traite comme des messages synchronisés). Telegram notifie normalement.

Configuration, par ordre de priorité :
  1. env MERLIN_NOTIFY_TELEGRAM_TOKEN + MERLIN_NOTIFY_TELEGRAM_CHAT
  2. data/notify.json : {"telegram": {"token": "123:ABC", "chat_id": "42"}}
iMessage (fallback si Telegram absent ou en échec) :
  1. env MERLIN_NOTIFY_IMESSAGE
  2. data/notify.json : {"imessage": "+33612345678"}
Aucun canal configuré : send() renvoie "disabled" sans rien faire.

Mise en place Telegram (une fois) :
  1. @BotFather → /newbot → récupérer le token.
  2. Envoyer n'importe quel message au bot depuis son téléphone.
  3. venv/bin/python notify.py chat-id   (affiche le chat_id à copier
     dans data/notify.json — le token doit déjà y être, ou en env).
Test manuel : venv/bin/python notify.py "coucou depuis Merlin"
"""
import json
import os
import subprocess
import sys
import urllib.request
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


def _config(config_file: Path) -> dict:
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def telegram_token(config_file: Path = CONFIG_FILE) -> str | None:
    env = os.getenv("MERLIN_NOTIFY_TELEGRAM_TOKEN", "").strip()
    if env:
        return env
    tg = _config(config_file).get("telegram")
    token = str(tg.get("token") or "").strip() if isinstance(tg, dict) else ""
    return token or None


def telegram_chat(config_file: Path = CONFIG_FILE) -> str | None:
    env = os.getenv("MERLIN_NOTIFY_TELEGRAM_CHAT", "").strip()
    if env:
        return env
    tg = _config(config_file).get("telegram")
    chat = str(tg.get("chat_id") or "").strip() if isinstance(tg, dict) else ""
    return chat or None


def recipient(config_file: Path = CONFIG_FILE) -> str | None:
    env = os.getenv("MERLIN_NOTIFY_IMESSAGE", "").strip()
    if env:
        return env
    value = str(_config(config_file).get("imessage") or "")
    return value.strip() or None


def send_telegram(text: str, config_file: Path = CONFIG_FILE) -> str:
    """Envoie `text` via l'API Bot Telegram.

    Retourne "sent", "disabled" (token/chat_id absents) ou "failed".
    """
    token, chat = telegram_token(config_file), telegram_chat(config_file)
    if not (token and chat):
        return "disabled"
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.loads(resp.read().decode("utf-8")).get("ok")
        return "sent" if ok else "failed"
    except Exception:
        return "failed"


def send_imessage(text: str, config_file: Path = CONFIG_FILE) -> str:
    """Envoie `text` au destinataire iMessage configuré.

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


def send(text: str, config_file: Path = CONFIG_FILE) -> str:
    """Telegram d'abord, iMessage en secours si Telegram absent ou en échec.

    Retourne "sent", "disabled" (aucun canal configuré) ou "failed".
    """
    tg = send_telegram(text, config_file)
    if tg == "sent":
        return "sent"
    im = send_imessage(text, config_file)
    if im == "sent":
        return "sent"
    return "disabled" if tg == "disabled" and im == "disabled" else "failed"


def _print_chat_ids(config_file: Path = CONFIG_FILE) -> int:
    """Affiche les chat_id vus par getUpdates (envoyer un message au bot avant)."""
    token = telegram_token(config_file)
    if not token:
        print("Pas de token Telegram (data/notify.json ou MERLIN_NOTIFY_TELEGRAM_TOKEN).")
        return 1
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=15
        ) as resp:
            updates = json.loads(resp.read().decode("utf-8")).get("result", [])
    except Exception as exc:
        print(f"getUpdates en échec : {exc}")
        return 1
    chats = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id") is not None:
            name = chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
            chats[chat["id"]] = name
    if not chats:
        print("Aucun message vu — écrire au bot depuis Telegram, puis relancer.")
        return 1
    for chat_id, name in chats.items():
        print(f"chat_id: {chat_id}  ({name})")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["chat-id"]:
        sys.exit(_print_chat_ids())
    message = " ".join(sys.argv[1:]) or "Test de notification Merlin."
    status = send(message)
    print(f"notify: {status}" + ("" if status == "sent" else
          " (configurer data/notify.json ou les env MERLIN_NOTIFY_*)" if status == "disabled" else
          " (token/chat_id Telegram ? autorisation Automation iMessage ?)"))
    sys.exit(0 if status == "sent" else 1)
