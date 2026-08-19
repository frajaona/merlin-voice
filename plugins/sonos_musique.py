"""Lancer de la musique sur les Sonos : résolveurs → lien de partage → HA.

Phase 2 de docs/SONOS.md. La demande vocale est résolue en lien de partage
(music.apple.com / open.spotify.com), puis jouée NATIVEMENT par l'enceinte
via `media_player.play_media` (verdict phase 0 : HA relaie les deux). Les
enceintes streament seules — rien ne dépend du Mac.

Résolveurs, dans l'ordre :
1. Alias `data/sonos-aliases.json` — {"nom parlé": "https://…"} : playlists
   des enfants, raccourcis du foyer. Match flou (accents/articles ignorés).
2. iTunes Search API (catalogue Apple Music, gratuit, sans clé) — albums,
   titres ; « joue <artiste> » choisit son album le plus en vue (un artiste
   n'a pas de lien de partage) et l'annonce.
3. Spotify Web API (client credentials) — albums/titres/artistes, et les
   playlists publiques quand l'utilisateur dit « sur Spotify ». Credentials :
   MERLIN_SPOTIFY_ID/SECRET ou data/spotify-app.json
   {"client_id": …, "client_secret": …} ; absent → résolveur désactivé.

Principe du gate : correspondance douteuse (score < 0.60) → on ne joue PAS,
on renvoie les candidats. Les playlists personnelles (Apple Music) ne sont
jouables qu'en phase 3 (AirPlay Music.app) — d'ici là : alias ou favori
Sonos. Bibliothèque NAS : phase 2b (le REST de HA ne sait pas la parcourir).

Pièce cible : `piece`, sinon MERLIN_SONOS_DEFAULT_ROOM, sinon l'unique
enceinte du foyer — sinon on demande.
"""

import asyncio
import difflib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from plugins import _sonos_common as common

SCHEMA = FunctionSchema(
    name="sonos_musique",
    description=(
        "Lance de la musique sur les enceintes Sonos : un album, un titre, "
        "un artiste ou une playlist, par nom, dans une pièce de la maison."
    ),
    properties={
        "recherche": {
            "type": "string",
            "description": (
                "Ce qu'il faut jouer : nom d'album, de titre, d'artiste ou "
                "de playlist (sans le nom de la pièce). Inclure l'artiste "
                "s'il est mentionné, ex : 'Discovery Daft Punk'."
            ),
        },
        "type": {
            "type": "string",
            "enum": ["album", "titre", "artiste", "playlist"],
            "description": "La nature de la demande, si elle est claire.",
        },
        "piece": {
            "type": "string",
            "description": "La pièce où jouer (ex : cuisine, salon).",
        },
        "service": {
            "type": "string",
            "enum": ["spotify"],
            "description": "Uniquement si l'utilisateur nomme Spotify.",
        },
    },
    required=["recherche"],
)

ALIAS_PATH = common.REPO / "data" / "sonos-aliases.json"
MATCH_MIN = 0.60          # sous ce score de correspondance : on demande
_ITUNES = "https://itunes.apple.com/search"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_API = "https://api.spotify.com/v1"

_spotify_token_cache: tuple[float, str] | None = None


def _http_json(url: str, data: bytes | None = None, headers: dict | None = None):
    """GET/POST JSON vers un service externe (iTunes, Spotify)."""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"service musique injoignable ({url.split('/')[2]}): {e}") from e


def _score(query: str, *candidates: str) -> float:
    """Correspondance floue query↔candidat, 0..1 (accents/articles ignorés)."""
    q = common.norm(query)
    best = 0.0
    for c in candidates:
        if not c:
            continue
        n = common.norm(c)
        r = difflib.SequenceMatcher(None, q, n).ratio()
        if q and (q in n or n in q):
            r = max(r, 0.85)
        best = max(best, r)
    return best


# -- résolveurs ---------------------------------------------------------------

def _match_alias(recherche: str):
    """(nom, url) du meilleur alias, ou None."""
    if not ALIAS_PATH.exists():
        return None
    try:
        aliases = json.loads(ALIAS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"sonos_musique: alias illisibles ({e})")
        return None
    best, best_score = None, 0.0
    for name, url in aliases.items():
        s = _score(recherche, name)
        if s > best_score:
            best, best_score = (name, url), s
    return best if best and best_score >= 0.75 else None


def _itunes_search(term: str, entity: str, attribute: str | None = None) -> list:
    params = {"term": term, "entity": entity, "country": "FR",
              "media": "music", "limit": 8}
    if attribute:
        params["attribute"] = attribute
    data = _http_json(f"{_ITUNES}?{urllib.parse.urlencode(params)}")
    return data.get("results", [])


def _resolve_apple(recherche: str, type_: str):
    """(url, description, candidats) — url None si rien d'assez sûr."""
    candidates = []  # (score, url, description humaine)
    if type_ == "artiste":
        for r in _itunes_search(recherche, "album", attribute="artistTerm"):
            s = _score(recherche, r.get("artistName", ""))
            desc = f"l'album {r.get('collectionName')} de {r.get('artistName')}"
            candidates.append((s, r.get("collectionViewUrl"), desc))
    else:
        if type_ in ("album", ""):
            for r in _itunes_search(recherche, "album"):
                s = _score(recherche, r.get("collectionName", ""),
                           f"{r.get('collectionName', '')} {r.get('artistName', '')}")
                desc = f"l'album {r.get('collectionName')} de {r.get('artistName')}"
                candidates.append((s, r.get("collectionViewUrl"), desc))
        if type_ in ("titre", ""):
            for r in _itunes_search(recherche, "song"):
                s = _score(recherche, r.get("trackName", ""),
                           f"{r.get('trackName', '')} {r.get('artistName', '')}")
                desc = f"{r.get('trackName')} de {r.get('artistName')}"
                candidates.append((s, r.get("trackViewUrl"), desc))
    return _best(candidates)


def _spotify_creds():
    cid = os.environ.get("MERLIN_SPOTIFY_ID", "").strip()
    secret = os.environ.get("MERLIN_SPOTIFY_SECRET", "").strip()
    if cid and secret:
        return cid, secret
    path = common.REPO / "data" / "spotify-app.json"
    if path.exists():
        try:
            d = json.loads(path.read_text())
            return d["client_id"], d["client_secret"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"sonos_musique: spotify-app.json illisible ({e})")
    return None


def _spotify_token() -> str:
    global _spotify_token_cache
    if _spotify_token_cache and time.monotonic() < _spotify_token_cache[0]:
        return _spotify_token_cache[1]
    creds = _spotify_creds()
    if not creds:
        raise RuntimeError("Spotify non configuré (data/spotify-app.json)")
    import base64
    basic = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    data = _http_json(
        _SPOTIFY_TOKEN_URL,
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    token = data["access_token"]
    _spotify_token_cache = (time.monotonic() + data.get("expires_in", 3600) - 60, token)
    return token


def _resolve_spotify(recherche: str, type_: str):
    types = {"album": "album", "titre": "track", "artiste": "artist",
             "playlist": "playlist"}.get(type_, "album,track")
    params = {"q": recherche, "type": types, "market": "FR", "limit": 8}
    data = _http_json(
        f"{_SPOTIFY_API}/search?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {_spotify_token()}"},
    )
    candidates = []
    for r in (data.get("albums") or {}).get("items", []):
        artists = ", ".join(a["name"] for a in r.get("artists", []))
        s = _score(recherche, r.get("name", ""), f"{r.get('name', '')} {artists}")
        candidates.append((s, r["external_urls"]["spotify"],
                           f"l'album {r.get('name')} de {artists}"))
    for r in (data.get("tracks") or {}).get("items", []):
        artists = ", ".join(a["name"] for a in r.get("artists", []))
        s = _score(recherche, r.get("name", ""), f"{r.get('name', '')} {artists}")
        candidates.append((s, r["external_urls"]["spotify"],
                           f"{r.get('name')} de {artists}"))
    for r in (data.get("playlists") or {}).get("items", []) or []:
        if not r:
            continue
        s = _score(recherche, r.get("name", ""))
        candidates.append((s, r["external_urls"]["spotify"],
                           f"la playlist {r.get('name')}"))
    if type_ == "artiste":
        # Un artiste n'a pas de lien jouable : prendre son meilleur album.
        for r in (data.get("artists") or {}).get("items", [])[:1]:
            if _score(recherche, r.get("name", "")) >= MATCH_MIN:
                albums = _http_json(
                    f"{_SPOTIFY_API}/artists/{r['id']}/albums?market=FR&limit=1&include_groups=album",
                    headers={"Authorization": f"Bearer {_spotify_token()}"},
                )
                for al in albums.get("items", []):
                    candidates.append((0.9, al["external_urls"]["spotify"],
                                       f"l'album {al.get('name')} de {r.get('name')}"))
    return _best(candidates)


def _best(candidates: list):
    """(url, desc, candidats_proches) — url None sous MATCH_MIN."""
    candidates = [c for c in candidates if c[1]]
    if not candidates:
        return None, None, []
    candidates.sort(key=lambda c: c[0], reverse=True)
    top = candidates[0]
    if top[0] >= MATCH_MIN:
        return top[1], top[2], []
    return None, None, [c[2] for c in candidates[:3]]


# -- lecture ------------------------------------------------------------------

def _target_room(piece: str, states: dict):
    if piece:
        return common.match_room(piece, states)
    default = os.environ.get("MERLIN_SONOS_DEFAULT_ROOM", "")
    if default:
        return common.match_room(default, states)
    if len(states) == 1:
        return next(iter(states)), None
    dispo = ", ".join(sorted(common.friendly(s) for s in states.values()))
    return None, f"précise la pièce ({dispo})"


def _run(recherche: str, type_: str, piece: str, service: str) -> dict:
    states = common.states()
    if not states:
        return {"error": "aucune enceinte Sonos vue par Home Assistant"}
    eid, err = _target_room(piece, states)
    if err:
        return {"error": err}
    nom = common.friendly(states[eid])

    # 1. Alias du foyer (playlists des enfants…), quel que soit le type.
    alias = _match_alias(recherche)
    if alias:
        url, label = alias[1], alias[0]
        common.call_service("play_media", eid, {
            "media_content_type": "music", "media_content_id": url})
        return {"ok": f"je lance {label} dans {nom}"}

    # 2. Playlists sans alias : Spotify explicite, sinon on n'improvise pas.
    if type_ == "playlist" and service != "spotify":
        return {"error": (
            f"je ne connais pas la playlist '{recherche}' — ajoute-la aux "
            "alias (data/sonos-aliases.json) ou aux favoris Sonos ; les "
            "playlists personnelles Apple Music arrivent en phase 3"
        )}

    # 3. Catalogues : Apple Music d'abord (service principal), Spotify en
    #    explicite ou en secours si configuré.
    url = desc = None
    close = []
    if service != "spotify":
        url, desc, close = _resolve_apple(recherche, type_)
        source = "Apple Music"
    if url is None and (service == "spotify" or _spotify_creds()):
        s_url, s_desc, s_close = _resolve_spotify(recherche, type_)
        if s_url:
            url, desc, source = s_url, s_desc, "Spotify"
        close = close or s_close

    if url is None:
        if close:
            return {"error": f"pas sûr de ce que tu veux — candidats : {'; '.join(close)}"}
        return {"error": f"rien trouvé pour '{recherche}'"}

    common.call_service("play_media", eid, {
        "media_content_type": "music", "media_content_id": url})
    return {"ok": f"je lance {desc} dans {nom}", "service": source}


async def handler(params: FunctionCallParams):
    recherche = (params.arguments.get("recherche") or "").strip()
    type_ = (params.arguments.get("type") or "").strip().lower()
    piece = (params.arguments.get("piece") or "").strip()
    service = (params.arguments.get("service") or "").strip().lower()
    logger.info(
        f"sonos_musique: recherche={recherche!r} type={type_!r} "
        f"piece={piece!r} service={service!r}"
    )
    if not recherche:
        await params.result_callback({"error": "dis-moi quoi jouer"})
        return
    await params.llm.push_frame(TTSSpeakFrame("Je lance ça."))
    try:
        result = await asyncio.to_thread(_run, recherche, type_, piece, service)
    except Exception as e:
        logger.warning(f"sonos_musique failed: {e}")
        result = {"error": str(e)}
    await params.result_callback(result)
