"""Contrôle des enceintes Sonos via Home Assistant (plan de contrôle REST).

Architecture et plan : docs/SONOS.md ; décision : docs/DECISIONS.md 2026-08-18.
Toutes les commandes sont des appels de service `media_player.*` sur le HA
Yellow — Merlin ne parle jamais UPnP lui-même. Les entités Sonos sont
découvertes via le template `integration_entities('sonos')` (fallback : tous
les media_player), les pièces sont matchées en français flou (accents et
articles ignorés). Principe du gate : en cas d'ambiguïté (plusieurs pièces
candidates), on n'agit PAS — on renvoie les choix possibles.

Env :
- MERLIN_HA_URL            base HA (défaut http://homeassistant.local:8123)
- MERLIN_HA_TOKEN          token longue durée (sinon lu dans data/ha-token)
- MERLIN_SONOS_DEFAULT_ROOM  pièce par défaut quand rien ne joue et
                             qu'aucune pièce n'est précisée
"""

import asyncio
import difflib
import json
import os
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

SCHEMA = FunctionSchema(
    name="sonos_controle",
    description=(
        "Contrôle les enceintes Sonos de la maison : lecture, pause, piste "
        "suivante/précédente, volume, muet, mode aléatoire, répétition, "
        "grouper/dégrouper des pièces, et savoir ce qui joue."
    ),
    properties={
        "action": {
            "type": "string",
            "enum": [
                "lecture", "pause", "suivant", "precedent", "volume",
                "muet", "aleatoire", "repetition", "grouper", "degrouper",
                "statut",
            ],
            "description": "L'action à effectuer sur les enceintes.",
        },
        "piece": {
            "type": "string",
            "description": (
                "La pièce visée (ex : cuisine, salon). Pour 'grouper' : la "
                "pièce à AJOUTER au groupe. Optionnel si une seule musique "
                "joue."
            ),
        },
        "valeur": {
            "type": "string",
            "description": (
                "Selon l'action — volume : '40', '+10', '-10' ; "
                "muet/aleatoire : 'on' ou 'off' ; repetition : 'off', "
                "'tout' ou 'une' ; grouper : la pièce dont la musique "
                "continue."
            ),
        },
    },
    required=["action"],
)

_REPO = Path(__file__).resolve().parent.parent
_TIMEOUT = 6
_ENTITY_CACHE_SECS = 600

_token_cache: str | None = None
_entity_cache: tuple[float, list[str]] | None = None


def _ha_url() -> str:
    # data/ha-url (IP conseillée) avant le défaut .local : la résolution mDNS
    # échoue depuis le process launchd du bot (Errno 8 après ~35 s, mesuré
    # 2026-08-18) alors qu'elle marche depuis un shell. Pas de DNS dans le
    # chemin chaud.
    url = os.environ.get("MERLIN_HA_URL", "").strip()
    if not url:
        path = _REPO / "data" / "ha-url"
        if path.exists():
            url = path.read_text().strip()
    return (url or "http://homeassistant.local:8123").rstrip("/")


def _get_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    token = os.environ.get("MERLIN_HA_TOKEN", "").strip()
    if not token:
        path = _REPO / "data" / "ha-token"
        if path.exists():
            token = path.read_text().strip()
    if not token:
        raise RuntimeError(
            "token Home Assistant manquant (data/ha-token ou MERLIN_HA_TOKEN)"
        )
    _token_cache = token
    return token


def _ha_request(method: str, path: str, payload: dict | None = None):
    """Un appel REST HA. Renvoie le JSON décodé (ou le texte brut)."""
    req = urllib.request.Request(
        _ha_url() + path,
        method=method,
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HA {path}: HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Home Assistant injoignable ({_ha_url()}): {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _sonos_entity_ids() -> list[str]:
    """Entités media_player de l'intégration sonos (cache 10 min)."""
    global _entity_cache
    if _entity_cache and time.monotonic() - _entity_cache[0] < _ENTITY_CACHE_SECS:
        return _entity_cache[1]
    ids: list[str] = []
    try:
        rendered = _ha_request(
            "POST", "/api/template",
            {"template": "{{ integration_entities('sonos') | tojson }}"},
        )
        parsed = json.loads(rendered) if isinstance(rendered, str) else rendered
        ids = [e for e in parsed if e.startswith("media_player.")]
    except Exception as e:  # template indisponible → fallback large
        logger.warning(f"sonos_controle: template integration_entities KO ({e})")
    if ids:
        _entity_cache = (time.monotonic(), ids)
    return ids


def _states() -> dict[str, dict]:
    """États des media_player Sonos : {entity_id: state_dict}."""
    all_states = _ha_request("GET", "/api/states")
    sonos_ids = set(_sonos_entity_ids())
    out = {}
    for s in all_states:
        eid = s.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        if sonos_ids and eid not in sonos_ids:
            continue
        out[eid] = s
    return out


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().strip()
    for art in ("l'", "la ", "le ", "les ", "du ", "de la ", "des "):
        if s.startswith(art):
            s = s[len(art):]
    return " ".join(s.split())


def _friendly(state: dict) -> str:
    return state.get("attributes", {}).get("friendly_name") or state["entity_id"]


def _match_room(piece: str, states: dict[str, dict]):
    """(entity_id, None) si match franc, sinon (None, message d'erreur)."""
    rooms = {_norm(_friendly(s)): eid for eid, s in states.items()}
    if not rooms:
        return None, "aucune enceinte Sonos trouvée dans Home Assistant"
    want = _norm(piece)
    if want in rooms:
        return rooms[want], None
    subs = [n for n in rooms if want in n or n in want]
    if len(subs) == 1:
        return rooms[subs[0]], None
    close = difflib.get_close_matches(want, list(rooms), n=2, cutoff=0.75)
    if len(close) == 1:
        return rooms[close[0]], None
    dispo = ", ".join(sorted(_friendly(s) for s in states.values()))
    return None, f"pièce '{piece}' ambiguë ou inconnue — enceintes : {dispo}"


def _pick_room(states: dict[str, dict], action: str):
    """Choix de pièce quand aucune n'est donnée. Préfère ne pas agir."""
    playing = [eid for eid, s in states.items() if s.get("state") == "playing"]
    if len(playing) == 1:
        return playing[0], None
    if len(playing) > 1:
        noms = ", ".join(_friendly(states[e]) for e in playing)
        return None, f"plusieurs pièces jouent ({noms}) — précise laquelle"
    if action == "lecture":
        paused = [eid for eid, s in states.items() if s.get("state") == "paused"]
        if len(paused) == 1:
            return paused[0], None
    default = os.environ.get("MERLIN_SONOS_DEFAULT_ROOM", "")
    if default:
        return _match_room(default, states)
    return None, "rien ne joue — précise la pièce"


def _call(service: str, entity_id: str, extra: dict | None = None):
    payload = {"entity_id": entity_id}
    if extra:
        payload.update(extra)
    _ha_request("POST", f"/api/services/media_player/{service}", payload)


def _vol_pct(state: dict) -> int:
    return round((state.get("attributes", {}).get("volume_level") or 0.0) * 100)


def _now_playing(state: dict) -> dict:
    a = state.get("attributes", {})
    return {
        "piece": _friendly(state),
        "etat": state.get("state"),
        "artiste": a.get("media_artist"),
        "titre": a.get("media_title"),
        "volume": _vol_pct(state),
    }


def _parse_volume(valeur: str, current_pct: int):
    v = _norm(valeur).replace("%", "").replace("pour cent", "").strip()
    if v in ("plus", "plus fort", "fort", "monte"):
        return min(100, current_pct + 10)
    if v in ("moins", "moins fort", "baisse"):
        return max(0, current_pct - 10)
    try:
        n = int(v.replace(" ", ""))
    except ValueError:
        return None
    if v.startswith(("+", "-")):
        return max(0, min(100, current_pct + n))
    return max(0, min(100, n))


_ON = ("on", "oui", "active", "actif", "vrai", "true", "1")
_OFF = ("off", "non", "desactive", "coupe", "faux", "false", "0")


def _run(action: str, piece: str, valeur: str) -> dict:
    """Cœur synchrone (exécuté via asyncio.to_thread)."""
    states = _states()
    if not states:
        return {"error": "aucune enceinte Sonos vue par Home Assistant"}

    if action == "statut":
        if piece:
            eid, err = _match_room(piece, states)
            if err:
                return {"error": err}
            s = states[eid]
            info = _now_playing(s)
            groupe = s.get("attributes", {}).get("group_members") or []
            if len(groupe) > 1:
                info["groupe"] = [
                    _friendly(states[g]) for g in groupe if g in states
                ]
            return info
        en_cours = [
            _now_playing(s) for s in states.values() if s.get("state") == "playing"
        ]
        if not en_cours:
            return {"statut": "rien ne joue", "pieces": sorted(_friendly(s) for s in states.values())}
        return {"en_cours": en_cours}

    if action == "grouper":
        if not piece:
            return {"error": "précise la pièce à ajouter au groupe"}
        membre, err = _match_room(piece, states)
        if err:
            return {"error": err}
        if valeur:
            maitre, err = _match_room(valeur, states)
            if err:
                return {"error": err}
        else:
            maitre, err = _pick_room(states, action)
            if err:
                return {"error": err}
        if maitre == membre:
            return {"error": "les deux pièces sont identiques"}
        _call("join", maitre, {"group_members": [membre]})
        return {"ok": f"{_friendly(states[membre])} rejoint {_friendly(states[maitre])}"}

    # Toutes les autres actions visent une seule pièce.
    if piece:
        eid, err = _match_room(piece, states)
    else:
        eid, err = _pick_room(states, action)
    if err:
        return {"error": err}
    s = states[eid]
    nom = _friendly(s)

    if action == "lecture":
        _call("media_play", eid)
        return {"ok": f"lecture dans {nom}"}
    if action == "pause":
        _call("media_pause", eid)
        return {"ok": f"pause dans {nom}"}
    if action == "suivant":
        _call("media_next_track", eid)
        return {"ok": f"piste suivante dans {nom}"}
    if action == "precedent":
        _call("media_previous_track", eid)
        return {"ok": f"piste précédente dans {nom}"}
    if action == "degrouper":
        _call("unjoin", eid)
        return {"ok": f"{nom} sort du groupe"}
    if action == "volume":
        if not valeur:
            return {"piece": nom, "volume": _vol_pct(s)}
        cible = _parse_volume(valeur, _vol_pct(s))
        if cible is None:
            return {"error": f"volume incompris : '{valeur}'"}
        _call("volume_set", eid, {"volume_level": cible / 100})
        return {"ok": f"volume {cible} dans {nom}"}
    if action == "muet":
        muet = _norm(valeur or "on") in _ON
        _call("volume_mute", eid, {"is_volume_muted": muet})
        return {"ok": f"{nom} {'en muet' if muet else 'son rétabli'}"}
    if action == "aleatoire":
        on = _norm(valeur or "on") in _ON
        _call("shuffle_set", eid, {"shuffle": on})
        return {"ok": f"aléatoire {'activé' if on else 'désactivé'} dans {nom}"}
    if action == "repetition":
        v = _norm(valeur or "")
        if v in ("tout", "all", "toutes", "playlist") or not v:
            mode = "all"
        elif v in ("une", "one", "un", "titre", "morceau", "un titre"):
            mode = "one"
        elif v in _OFF or v in ("aucune", "arrete"):
            mode = "off"
        else:
            return {"error": f"répétition incomprise : '{valeur}' (off/tout/une)"}
        _call("repeat_set", eid, {"repeat": mode})
        return {"ok": f"répétition '{mode}' dans {nom}"}

    return {"error": f"action inconnue : {action}"}


async def handler(params: FunctionCallParams):
    action = (params.arguments.get("action") or "").strip().lower()
    piece = (params.arguments.get("piece") or "").strip()
    valeur = (params.arguments.get("valeur") or "").strip()
    logger.info(f"sonos_controle: action={action} piece={piece!r} valeur={valeur!r}")
    try:
        result = await asyncio.to_thread(_run, action, piece, valeur)
    except Exception as e:
        logger.warning(f"sonos_controle failed: {e}")
        result = {"error": str(e)}
    await params.result_callback(result)
