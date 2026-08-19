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
4. Bibliothèque NAS (phase 2b) — l'index Sonos, via le websocket HA
   (cache disque quotidien `data/sonos-library.json`, re-scan MANUEL par
   le bouton « 🔄 NAS » du dashboard — jamais automatique) : artistes et
   albums jouables comme conteneurs, playlists iTunes importées, pistes
   (listing plafonné à ~1000 par Sonos — best effort). « depuis le NAS /
   la bibliothèque » (service=nas) force ce résolveur ; sinon il passe en
   secours derrière les catalogues.
5. Favoris Sonos (même canal) — tout ce qui est étoilé dans l'app Sonos,
   dont les playlists Sonos (SQ:n) : c'est le chemin des playlists perso
   en attendant la phase 3.

Principe du gate : correspondance douteuse (score < 0.60) → on ne joue PAS,
on renvoie les candidats. Playlists : alias → favoris Sonos → playlists
NAS → **playlists perso Music.app** (noms scrapés en AppleScript, cache
quotidien — reconnues mais jouables seulement en phase 3 : réponse honnête
+ conseil favori, et le catalogue ne peut pas les détourner) → **catalogue
Apple Music via MusicKit** (service principal, avant Spotify — voir
data/musickit.json plus bas) → Spotify explicite — sinon refus (on
n'improvise pas sur les playlists publiques).

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
            "enum": ["spotify", "nas"],
            "description": (
                "'spotify' si l'utilisateur nomme Spotify ; 'nas' s'il dit "
                "« depuis le NAS », « la bibliothèque » ou « nos disques »."
            ),
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


def _link(url: str | None):
    """Un lien de partage devient une ref jouable (type, id) — ou None."""
    return ("music", url) if url else None


def _resolve_apple(recherche: str, type_: str):
    """(ref, description, candidats) — ref None si rien d'assez sûr."""
    candidates = []  # (score, ref jouable, description humaine)
    if type_ == "artiste":
        for r in _itunes_search(recherche, "album", attribute="artistTerm"):
            s = _score(recherche, r.get("artistName", ""))
            desc = f"l'album {r.get('collectionName')} de {r.get('artistName')}"
            candidates.append((s, _link(r.get("collectionViewUrl")), desc))
    else:
        if type_ in ("album", ""):
            for r in _itunes_search(recherche, "album"):
                s = _score(recherche, r.get("collectionName", ""),
                           f"{r.get('collectionName', '')} {r.get('artistName', '')}")
                desc = f"l'album {r.get('collectionName')} de {r.get('artistName')}"
                candidates.append((s, _link(r.get("collectionViewUrl")), desc))
        if type_ in ("titre", ""):
            for r in _itunes_search(recherche, "song"):
                s = _score(recherche, r.get("trackName", ""),
                           f"{r.get('trackName', '')} {r.get('artistName', '')}")
                desc = f"{r.get('trackName')} de {r.get('artistName')}"
                candidates.append((s, _link(r.get("trackViewUrl")), desc))
    return _best(candidates)


# -- MusicKit (playlists du catalogue Apple Music) ----------------------------
# L'iTunes Search API n'a PAS d'entité playlist — la recherche de playlists
# Apple Music passe par l'API officielle MusicKit, avec un simple token
# développeur (JWT ES256 signé avec une clé MusicKit du portail développeur ;
# pas de login utilisateur pour le catalogue). Config :
# data/musickit.json {"team_id": …, "key_id": …, "private_key": "-----BEGIN…"}
# — absent → résolveur désactivé (favoris/NAS/Spotify continuent).
_MUSICKIT_SEARCH = "https://api.music.apple.com/v1/catalog/fr/search"
_musickit_token_cache: tuple[float, str] | None = None


def _musickit_creds():
    path = common.REPO / "data" / "musickit.json"
    if path.exists():
        try:
            d = json.loads(path.read_text())
            return d["team_id"], d["key_id"], d["private_key"]
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"sonos_musique: musickit.json illisible ({e})")
    return None


def _musickit_token() -> str:
    """JWT développeur ES256 (cryptography est déjà une dépendance aiortc)."""
    global _musickit_token_cache
    if _musickit_token_cache and time.monotonic() < _musickit_token_cache[0]:
        return _musickit_token_cache[1]
    creds = _musickit_creds()
    if not creds:
        raise RuntimeError("MusicKit non configuré (data/musickit.json)")
    team_id, key_id, pem = creds
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    def b64(d: bytes) -> bytes:
        return base64.urlsafe_b64encode(d).rstrip(b"=")

    key = serialization.load_pem_private_key(pem.encode(), password=None)
    now = int(time.time())
    signing = (b64(json.dumps({"alg": "ES256", "kid": key_id}).encode())
               + b"." + b64(json.dumps({"iss": team_id, "iat": now,
                                        "exp": now + 12 * 3600}).encode()))
    # JWT veut la signature brute r||s (64 octets), pas le DER de cryptography.
    r, s = utils.decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    token = (signing + b"." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()
    _musickit_token_cache = (time.monotonic() + 12 * 3600 - 120, token)
    return token


def _resolve_musicapp(recherche: str) -> str | None:
    """Nom de la playlist perso Apple Music (Music.app) qui matche, sinon
    None. Résolution seulement — pas jouable avant la phase 3 — mais elle
    doit passer AVANT le catalogue MusicKit : « ma playlist jogging » ne
    doit pas être détournée par une playlist éditoriale au nom proche."""
    try:
        names = common.musicapp_playlists()
    except Exception as e:
        logger.warning(f"sonos_musique: playlists Music.app indisponibles ({e})")
        return None
    best, best_score = None, 0.0
    for n in names:
        s = _score(recherche, n)
        if s > best_score:
            best, best_score = n, s
    return best if best_score >= MATCH_MIN else None


def _resolve_apple_playlist(recherche: str):
    """Playlists du catalogue Apple Music (éditoriales) via MusicKit."""
    params = {"term": recherche, "types": "playlists", "limit": 8}
    data = _http_json(
        f"{_MUSICKIT_SEARCH}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {_musickit_token()}"},
    )
    candidates = []
    playlists = ((data.get("results") or {}).get("playlists") or {}).get("data", [])
    for p in playlists:
        a = p.get("attributes", {})
        s = _score(recherche, a.get("name", ""))
        candidates.append((s, _link(a.get("url")), f"la playlist {a.get('name')}"))
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
        candidates.append((s, _link(r["external_urls"]["spotify"]),
                           f"l'album {r.get('name')} de {artists}"))
    for r in (data.get("tracks") or {}).get("items", []):
        artists = ", ".join(a["name"] for a in r.get("artists", []))
        s = _score(recherche, r.get("name", ""), f"{r.get('name', '')} {artists}")
        candidates.append((s, _link(r["external_urls"]["spotify"]),
                           f"{r.get('name')} de {artists}"))
    for r in (data.get("playlists") or {}).get("items", []) or []:
        if not r:
            continue
        s = _score(recherche, r.get("name", ""))
        candidates.append((s, _link(r["external_urls"]["spotify"]),
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
                    candidates.append((0.9, _link(al["external_urls"]["spotify"]),
                                       f"l'album {al.get('name')} de {r.get('name')}"))
    return _best(candidates)


# -- bibliothèque NAS et favoris (websocket HA, caches) -----------------------

# Favoris : cache mémoire court (on étoile souvent des choses dans l'app).
_browse_cache: dict[tuple, tuple[float, list]] = {}
_BROWSE_TTL = 600

# Bibliothèque NAS : cache disque quotidien dans _sonos_common (partagé avec
# le bouton « 🔄 NAS » du dashboard) — rafraîchissement MANUEL uniquement,
# décision Fred 19/08 : pas de re-scan automatique sur échec.


def _browse(eid: str, content_type: str, content_id: str) -> list[dict]:
    key = (content_type, content_id)
    hit = _browse_cache.get(key)
    if hit and time.monotonic() - hit[0] < _BROWSE_TTL:
        return hit[1]
    kids = common.ws_browse(eid, content_type, content_id)
    _browse_cache[key] = (time.monotonic(), kids)
    return kids


def _album_artist(item: dict) -> str:
    """L'artiste d'un album NAS vit dans l'id : A:ALBUM/<album>/<artiste>."""
    parts = (item.get("media_content_id") or "").split("/")
    return urllib.parse.unquote(parts[2]) if len(parts) >= 3 else ""


def _resolve_nas(recherche: str, type_: str, eid: str):
    """Cherche dans l'index Sonos du NAS (cache disque quotidien de
    _sonos_common — re-scan manuel via le dashboard). Pistes seulement en
    explicite (listing plafonné ~1000 par Sonos, et lourd)."""
    types = [type_] if type_ else ["artiste", "album"]
    candidates = []
    for t in types:
        ct, ci = common.NAS_CATEGORIES[t]
        seen = set()
        for c in common.nas_browse(eid, ct, ci):
            title = c.get("title", "")
            k = common.norm(title)
            if not title or k in seen:  # playlists iTunes en double, etc.
                continue
            seen.add(k)
            if t == "album":
                artiste = _album_artist(c)
                s = _score(recherche, title, f"{title} {artiste}")
                desc = f"l'album {title}" + (f" de {artiste}" if artiste else "")
            elif t == "artiste":
                s = _score(recherche, title)
                desc = f"les albums de {title}"
            else:
                s = _score(recherche, title)
                desc = ("la playlist " if t == "playlist" else "") + title
            candidates.append(
                (s, (c["media_content_type"], c["media_content_id"]), desc))
    return _best(candidates)


def _resolve_favoris(recherche: str, eid: str):
    """Tout ce qui est étoilé dans l'app Sonos (dont les playlists SQ:n)."""
    candidates = []
    for folder in _browse(eid, "favorites", ""):
        for c in _browse(eid, folder["media_content_type"],
                         folder["media_content_id"]):
            s = _score(recherche, c.get("title", ""))
            candidates.append(
                (s, (c["media_content_type"], c["media_content_id"]),
                 f"le favori Sonos {c.get('title')}"))
    return _best(candidates)


def _best(candidates: list):
    """(ref, desc, candidats_proches) — ref None sous MATCH_MIN."""
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


def _play(eid: str, ref: tuple):
    common.call_service("play_media", eid, {
        "media_content_type": ref[0], "media_content_id": ref[1]})


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
        _play(eid, _link(alias[1]))
        return {"ok": f"je lance {alias[0]} dans {nom}"}

    # 2. NAS explicite (« depuis le NAS / la bibliothèque »).
    if service == "nas":
        ref, desc, close = _resolve_nas(recherche, type_, eid)
        if ref is None:
            extra = f" — candidats : {'; '.join(close)}" if close else ""
            return {"error": f"rien de sûr pour '{recherche}' sur le NAS{extra}"}
        _play(eid, ref)
        return {"ok": f"je lance {desc} dans {nom}", "service": "bibliothèque NAS"}

    # 3. Playlists : favoris Sonos → playlists NAS → catalogue Apple Music
    #    (MusicKit, service principal — AVANT Spotify, demande Fred 19/08) →
    #    Spotify explicite — sinon refus (on n'improvise pas).
    if type_ == "playlist":
        source = "favoris Sonos"
        ref, desc, _ = _resolve_favoris(recherche, eid)
        if ref is None:
            ref, desc, _ = _resolve_nas(recherche, "playlist", eid)
            source = "bibliothèque NAS"
        if ref is None and service != "spotify":
            perso = _resolve_musicapp(recherche)
            if perso:
                return {"error": (
                    f"« {perso} » est une playlist personnelle Apple Music — "
                    "pas encore jouable sur Sonos (phase 3) ; ajoute-la aux "
                    "favoris Sonos pour que je puisse la lancer"
                )}
        if ref is None and service != "spotify" and _musickit_creds():
            ref, desc, _ = _resolve_apple_playlist(recherche)
            source = "Apple Music"
        if ref is None and service == "spotify":
            ref, desc, _ = _resolve_spotify(recherche, "playlist")
            source = "Spotify"
        if ref is None:
            return {"error": (
                f"je ne connais pas la playlist '{recherche}' — ajoute-la "
                "aux alias (data/sonos-aliases.json) ou aux favoris Sonos ; "
                "les playlists personnelles Apple Music arrivent en phase 3"
            )}
        _play(eid, ref)
        return {"ok": f"je lance {desc} dans {nom}", "service": source}

    # 4. Catalogues : Apple Music d'abord (service principal), Spotify en
    #    explicite ou en secours si configuré — puis NAS et favoris.
    ref = desc = None
    source = ""
    close = []
    if service != "spotify":
        ref, desc, close = _resolve_apple(recherche, type_)
        source = "Apple Music"
    if ref is None and (service == "spotify" or _spotify_creds()):
        s_ref, s_desc, s_close = _resolve_spotify(recherche, type_)
        if s_ref:
            ref, desc, source = s_ref, s_desc, "Spotify"
        close = close or s_close
    if ref is None and service != "spotify":
        n_ref, n_desc, n_close = _resolve_nas(recherche, type_, eid)
        if n_ref:
            ref, desc, source = n_ref, n_desc, "bibliothèque NAS"
        close = close or n_close
        if ref is None:
            f_ref, f_desc, _ = _resolve_favoris(recherche, eid)
            if f_ref:
                ref, desc, source = f_ref, f_desc, "favoris Sonos"

    if ref is None:
        if close:
            return {"error": f"pas sûr de ce que tu veux — candidats : {'; '.join(close)}"}
        return {"error": f"rien trouvé pour '{recherche}'"}

    _play(eid, ref)
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
