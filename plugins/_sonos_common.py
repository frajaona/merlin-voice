"""Plomberie Sonos partagée par les plugins sonos_*.

Fichier préfixé `_` : jamais chargé comme plugin (voir plugins/README.md).
Extrait de sonos_controle.py pour la phase 2 (docs/SONOS.md). Le client HA
générique (URL/token/REST, norm, friendly) vit dans _ha_common.py depuis le
2026-08-21 (partagé avec le plugin home_assistant) — ré-exporté ici pour que
les appelants et les tests (`common.ha_request`) ne bougent pas. Tout est
synchrone (urllib) — les handlers appellent via asyncio.to_thread.

Env / fichiers : voir _ha_common.py (MERLIN_HA_URL/TOKEN), plus
- MERLIN_SONOS_DEFAULT_ROOM : pièce par défaut
"""

import asyncio
import difflib
import json
import os
import time

from loguru import logger

from plugins._ha_common import (  # noqa: F401 — ré-export (tests/appelants)
    REPO, TIMEOUT, friendly, get_token, ha_request, ha_url, norm,
)

_ENTITY_CACHE_SECS = 600

_entity_cache: tuple[float, list[str]] | None = None


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


def ws_browse(entity_id: str, media_content_type: str | None = None,
              media_content_id: str | None = None) -> list[dict]:
    """Enfants d'un nœud media (bibliothèque Sonos, favoris…).

    Le REST de HA ne sait pas parcourir les médias — c'est du websocket
    uniquement (`media_player/browse_media`). Appelé depuis les handlers
    via asyncio.to_thread : le thread n'a pas de boucle, asyncio.run est
    sûr ici. Connexion éphémère par appel — les résultats sont mis en
    cache côté appelant (la bibliothèque bouge rarement)."""
    return asyncio.run(_ws_browse(entity_id, media_content_type, media_content_id))


async def _ws_browse(entity_id, content_type, content_id) -> list[dict]:
    import aiohttp

    ws_url = ha_url().replace("http", "ws", 1) + "/api/websocket"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                await ws.receive_json(timeout=TIMEOUT)  # auth_required
                await ws.send_json({"type": "auth", "access_token": get_token()})
                msg = await ws.receive_json(timeout=TIMEOUT)
                if msg.get("type") != "auth_ok":
                    raise RuntimeError("HA websocket : auth refusée")
                payload = {"id": 1, "type": "media_player/browse_media",
                           "entity_id": entity_id}
                if content_type is not None:
                    payload["media_content_type"] = content_type
                if content_id is not None:
                    payload["media_content_id"] = content_id
                await ws.send_json(payload)
                # Un gros listing (pistes) peut prendre quelques secondes.
                msg = await ws.receive_json(timeout=20)
                if not msg.get("success"):
                    err = (msg.get("error") or {}).get("message", "échec")
                    raise RuntimeError(f"HA browse: {err}")
                return (msg.get("result") or {}).get("children") or []
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise RuntimeError(f"Home Assistant websocket injoignable: {e}") from e


# -- bibliothèque NAS (index Sonos) : cache disque quotidien -------------------
# La bibliothèque ne bouge presque jamais (demande Fred 19/08) : cache disque
# TTL 24 h, survit aux redémarrages. Rafraîchissement MANUEL : bouton
# « 🔄 NAS » du dashboard → POST /api/sonos/refresh → refresh_library().
# Vit ici (et pas dans sonos_musique) pour être joignable depuis
# dashboard_api sans passer par le chargeur de plugins.

LIBRARY_CACHE_PATH = REPO / "data" / "sonos-library.json"
LIBRARY_TTL = float(os.environ.get("MERLIN_SONOS_LIBRARY_TTL", "86400"))
NAS_CATEGORIES = {
    "artiste": ("artist", "A:ALBUMARTIST"),
    "album": ("album", "A:ALBUM"),
    "titre": ("track", "A:TRACKS"),
    "playlist": ("playlist", "A:PLAYLISTS"),
}
_library_mem: dict | None = None  # miroir mémoire du fichier


def library_load() -> dict:
    global _library_mem
    if _library_mem is None:
        try:
            _library_mem = json.loads(LIBRARY_CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            _library_mem = {}
        _library_mem.setdefault("categories", {})
    return _library_mem


def nas_browse(entity_id: str, content_type: str, content_id: str,
               force: bool = False) -> list[dict]:
    lib = library_load()
    cat = lib["categories"].get(content_id)
    if not force and cat and time.time() - cat.get("at", 0) < LIBRARY_TTL:
        return cat["items"]
    items = [
        {"title": c.get("title", ""),
         "media_content_type": c.get("media_content_type"),
         "media_content_id": c.get("media_content_id")}
        for c in ws_browse(entity_id, content_type, content_id)
    ]
    lib["categories"][content_id] = {"at": time.time(), "items": items}
    try:
        LIBRARY_CACHE_PATH.write_text(json.dumps(lib, ensure_ascii=False))
    except OSError as e:
        logger.warning(f"sonos: cache bibliothèque non écrit ({e})")
    return items


# -- playlists personnelles Apple Music (Music.app, AppleScript) ---------------
# La bibliothèque iCloud est synchronisée dans Music.app sur ce Mac : on
# scrape les NOMS des playlists perso (résolution seulement — la lecture
# reste phase 3, pas de lien de partage public). Même cache quotidien que
# la bibliothèque NAS, rafraîchi par le même bouton « 🔄 NAS ».

MUSICAPP_CACHE_PATH = REPO / "data" / "musicapp-playlists.json"
_musicapp_mem: dict | None = None

_MUSICAPP_SCRIPT = """\
with timeout of 30 seconds
  tell application "Music" to set pl to name of user playlists whose special kind is none
end timeout
set AppleScript's text item delimiters to linefeed
pl as text"""


def _osascript(script: str, timeout: int = 40) -> str:
    import subprocess

    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"osascript: {r.stderr.strip() or f'code {r.returncode}'}")
    return r.stdout.rstrip("\n")


def musicapp_playlists(force: bool = False) -> list[str]:
    """Noms des playlists perso Apple Music (cache disque quotidien)."""
    global _musicapp_mem
    if _musicapp_mem is None:
        try:
            _musicapp_mem = json.loads(MUSICAPP_CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            _musicapp_mem = {}
    if not force and _musicapp_mem.get("items") is not None \
            and time.time() - _musicapp_mem.get("at", 0) < LIBRARY_TTL:
        return _musicapp_mem["items"]
    out = _osascript(_MUSICAPP_SCRIPT)
    names = sorted({n.strip() for n in out.split("\n") if n.strip()})
    _musicapp_mem = {"at": time.time(), "items": names}
    try:
        MUSICAPP_CACHE_PATH.write_text(
            json.dumps(_musicapp_mem, ensure_ascii=False))
    except OSError as e:
        logger.warning(f"sonos: cache playlists Music.app non écrit ({e})")
    logger.info(f"sonos: {len(names)} playlists perso Music.app scannées")
    return names


def refresh_library(entity_id: str | None = None) -> dict:
    """Re-scanne les 4 catégories de l'index NAS. Renvoie les comptes."""
    if entity_id is None:
        sts = states()
        if not sts:
            raise RuntimeError("aucune enceinte Sonos vue par Home Assistant")
        entity_id = sorted(sts)[0]
    counts = {}
    for label, (ct, ci) in NAS_CATEGORIES.items():
        counts[label + "s"] = len(nas_browse(entity_id, ct, ci, force=True))
    # Best-effort : Music.app peut être indisponible sans casser le scan NAS.
    try:
        counts["playlists perso"] = len(musicapp_playlists(force=True))
    except Exception as e:
        logger.warning(f"sonos: scrape Music.app échoué ({e})")
    logger.info(f"sonos: bibliothèque re-scannée ({counts})")
    return counts
