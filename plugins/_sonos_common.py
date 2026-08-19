"""Plomberie Home Assistant / Sonos partagée par les plugins sonos_*.

Fichier préfixé `_` : jamais chargé comme plugin (voir plugins/README.md).
Extrait de sonos_controle.py pour la phase 2 (docs/SONOS.md). Tout est
synchrone (urllib) — les handlers appellent via asyncio.to_thread.

Env / fichiers :
- MERLIN_HA_URL, sinon data/ha-url (IP conseillée — le mDNS .local échoue
  depuis le process launchd du bot, mesuré 2026-08-18), sinon
  http://homeassistant.local:8123
- MERLIN_HA_TOKEN, sinon data/ha-token
- MERLIN_SONOS_DEFAULT_ROOM : pièce par défaut
"""

import difflib
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from loguru import logger

REPO = Path(__file__).resolve().parent.parent
TIMEOUT = 6
_ENTITY_CACHE_SECS = 600

_token_cache: str | None = None
_entity_cache: tuple[float, list[str]] | None = None


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


def sonos_entity_ids() -> list[str]:
    """Entités media_player de l'intégration sonos (cache 10 min)."""
    global _entity_cache
    if _entity_cache and time.monotonic() - _entity_cache[0] < _ENTITY_CACHE_SECS:
        return _entity_cache[1]
    ids: list[str] = []
    try:
        rendered = ha_request(
            "POST", "/api/template",
            {"template": "{{ integration_entities('sonos') | tojson }}"},
        )
        parsed = json.loads(rendered) if isinstance(rendered, str) else rendered
        ids = [e for e in parsed if e.startswith("media_player.")]
    except Exception as e:  # template indisponible → fallback large
        logger.warning(f"sonos: template integration_entities KO ({e})")
    if ids:
        _entity_cache = (time.monotonic(), ids)
    return ids


def states() -> dict[str, dict]:
    """États des media_player Sonos : {entity_id: state_dict}."""
    all_states = ha_request("GET", "/api/states")
    ids = set(sonos_entity_ids())
    out = {}
    for s in all_states:
        eid = s.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        if ids and eid not in ids:
            continue
        out[eid] = s
    return out


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().strip()
    for art in ("l'", "la ", "le ", "les ", "du ", "de la ", "des "):
        if s.startswith(art):
            s = s[len(art):]
    return " ".join(s.split())


def friendly(state: dict) -> str:
    return state.get("attributes", {}).get("friendly_name") or state["entity_id"]


def match_room(piece: str, sts: dict[str, dict]):
    """(entity_id, None) si match franc, sinon (None, message d'erreur)."""
    rooms = {norm(friendly(s)): eid for eid, s in sts.items()}
    if not rooms:
        return None, "aucune enceinte Sonos trouvée dans Home Assistant"
    want = norm(piece)
    if want in rooms:
        return rooms[want], None
    subs = [n for n in rooms if want in n or n in want]
    if len(subs) == 1:
        return rooms[subs[0]], None
    close = difflib.get_close_matches(want, list(rooms), n=2, cutoff=0.75)
    if len(close) == 1:
        return rooms[close[0]], None
    dispo = ", ".join(sorted(friendly(s) for s in sts.values()))
    return None, f"pièce '{piece}' ambiguë ou inconnue — enceintes : {dispo}"


def pick_room(sts: dict[str, dict], action: str):
    """Choix de pièce quand aucune n'est donnée. Préfère ne pas agir."""
    playing = [eid for eid, s in sts.items() if s.get("state") == "playing"]
    if len(playing) == 1:
        return playing[0], None
    if len(playing) > 1:
        noms = ", ".join(friendly(sts[e]) for e in playing)
        return None, f"plusieurs pièces jouent ({noms}) — précise laquelle"
    if action == "lecture":
        paused = [eid for eid, s in sts.items() if s.get("state") == "paused"]
        if len(paused) == 1:
            return paused[0], None
    default = os.environ.get("MERLIN_SONOS_DEFAULT_ROOM", "")
    if default:
        return match_room(default, sts)
    return None, "rien ne joue — précise la pièce"


def call_service(service: str, entity_id: str, extra: dict | None = None):
    payload = {"entity_id": entity_id}
    if extra:
        payload.update(extra)
    ha_request("POST", f"/api/services/media_player/{service}", payload)


def vol_pct(state: dict) -> int:
    return round((state.get("attributes", {}).get("volume_level") or 0.0) * 100)
