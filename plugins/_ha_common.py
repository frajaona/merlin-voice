"""Client Home Assistant générique, partagé par les plugins qui parlent au
HA Yellow (sonos_* via _sonos_common, home_assistant).

Fichier préfixé `_` : jamais chargé comme plugin (voir plugins/README.md).
Extrait de _sonos_common.py le 2026-08-21 pour l'outil home_assistant —
c'était le « client HA réutilisable » promis par la phase 1 de docs/SONOS.md.
Tout est synchrone (urllib) — les handlers appellent via asyncio.to_thread.

Env / fichiers :
- MERLIN_HA_URL, sinon data/ha-url (IP conseillée — le mDNS .local échoue
  depuis le process launchd du bot, mesuré 2026-08-18), sinon
  http://homeassistant.local:8123
- MERLIN_HA_TOKEN, sinon data/ha-token
"""

import json
import os
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TIMEOUT = 6

_token_cache: str | None = None


def ha_url() -> str:
    url = os.environ.get("MERLIN_HA_URL", "").strip()
    if not url:
        path = REPO / "data" / "ha-url"
        if path.exists():
            url = path.read_text().strip()
    return (url or "http://homeassistant.local:8123").rstrip("/")


def get_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    token = os.environ.get("MERLIN_HA_TOKEN", "").strip()
    if not token:
        path = REPO / "data" / "ha-token"
        if path.exists():
            token = path.read_text().strip()
    if not token:
        raise RuntimeError(
            "token Home Assistant manquant (data/ha-token ou MERLIN_HA_TOKEN)"
        )
    _token_cache = token
    return token


def ha_request(method: str, path: str, payload: dict | None = None):
    """Un appel REST HA. Renvoie le JSON décodé (ou le texte brut)."""
    req = urllib.request.Request(
        ha_url() + path,
        method=method,
        headers={
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HA {path}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Home Assistant injoignable ({ha_url()}): {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().strip()
    for art in ("l'", "la ", "le ", "les ", "du ", "de la ", "des "):
        if s.startswith(art):
            s = s[len(art):]
    return " ".join(s.split())


def friendly(state: dict) -> str:
    return state.get("attributes", {}).get("friendly_name") or state["entity_id"]
