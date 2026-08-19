"""Contrôle des enceintes Sonos via Home Assistant (plan de contrôle REST).

Architecture et plan : docs/SONOS.md ; décisions : docs/DECISIONS.md
2026-08-18/19. Toutes les commandes sont des appels de service
`media_player.*` sur le HA Yellow — Merlin ne parle jamais UPnP lui-même.
Découverte des entités, matching des pièces en français flou et plomberie
REST : plugins/_sonos_common.py (partagé avec sonos_musique). Principe du
gate : en cas d'ambiguïté (plusieurs pièces candidates), on n'agit PAS —
on renvoie les choix possibles.

Env : voir _sonos_common.py (MERLIN_HA_URL/TOKEN, MERLIN_SONOS_DEFAULT_ROOM).
"""

import asyncio

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from plugins import _sonos_common as common

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


def _now_playing(state: dict) -> dict:
    a = state.get("attributes", {})
    return {
        "piece": common.friendly(state),
        "etat": state.get("state"),
        "artiste": a.get("media_artist"),
        "titre": a.get("media_title"),
        "volume": common.vol_pct(state),
    }


def _parse_volume(valeur: str, current_pct: int):
    v = common.norm(valeur).replace("%", "").replace("pour cent", "").strip()
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
    states = common.states()
    if not states:
        return {"error": "aucune enceinte Sonos vue par Home Assistant"}

    if action == "statut":
        if piece:
            eid, err = common.match_room(piece, states)
            if err:
                return {"error": err}
            s = states[eid]
            info = _now_playing(s)
            groupe = s.get("attributes", {}).get("group_members") or []
            if len(groupe) > 1:
                info["groupe"] = [
                    common.friendly(states[g]) for g in groupe if g in states
                ]
            return info
        en_cours = [
            _now_playing(s) for s in states.values() if s.get("state") == "playing"
        ]
        if not en_cours:
            return {"statut": "rien ne joue", "pieces": sorted(common.friendly(s) for s in states.values())}
        return {"en_cours": en_cours}

    if action == "grouper":
        if not piece:
            return {"error": "précise la pièce à ajouter au groupe"}
        membre, err = common.match_room(piece, states)
        if err:
            return {"error": err}
        if valeur:
            maitre, err = common.match_room(valeur, states)
            if err:
                return {"error": err}
        else:
            maitre, err = common.pick_room(states, action)
            if err:
                return {"error": err}
        if maitre == membre:
            return {"error": "les deux pièces sont identiques"}
        common.call_service("join", maitre, {"group_members": [membre]})
        return {"ok": f"{common.friendly(states[membre])} rejoint {common.friendly(states[maitre])}"}

    # Toutes les autres actions visent une seule pièce.
    if piece:
        eid, err = common.match_room(piece, states)
    else:
        eid, err = common.pick_room(states, action)
    if err:
        return {"error": err}
    s = states[eid]
    nom = common.friendly(s)

    if action == "lecture":
        common.call_service("media_play", eid)
        return {"ok": f"lecture dans {nom}"}
    if action == "pause":
        common.call_service("media_pause", eid)
        return {"ok": f"pause dans {nom}"}
    if action == "suivant":
        common.call_service("media_next_track", eid)
        return {"ok": f"piste suivante dans {nom}"}
    if action == "precedent":
        common.call_service("media_previous_track", eid)
        return {"ok": f"piste précédente dans {nom}"}
    if action == "degrouper":
        common.call_service("unjoin", eid)
        return {"ok": f"{nom} sort du groupe"}
    if action == "volume":
        if not valeur:
            return {"piece": nom, "volume": common.vol_pct(s)}
        cible = _parse_volume(valeur, common.vol_pct(s))
        if cible is None:
            return {"error": f"volume incompris : '{valeur}'"}
        common.call_service("volume_set", eid, {"volume_level": cible / 100})
        return {"ok": f"volume {cible} dans {nom}"}
    if action == "muet":
        muet = common.norm(valeur or "on") in _ON
        common.call_service("volume_mute", eid, {"is_volume_muted": muet})
        return {"ok": f"{nom} {'en muet' if muet else 'son rétabli'}"}
    if action == "aleatoire":
        on = common.norm(valeur or "on") in _ON
        common.call_service("shuffle_set", eid, {"shuffle": on})
        return {"ok": f"aléatoire {'activé' if on else 'désactivé'} dans {nom}"}
    if action == "repetition":
        v = common.norm(valeur or "")
        if v in ("tout", "all", "toutes", "playlist") or not v:
            mode = "all"
        elif v in ("une", "one", "un", "titre", "morceau", "un titre"):
            mode = "one"
        elif v in _OFF or v in ("aucune", "arrete"):
            mode = "off"
        else:
            return {"error": f"répétition incomprise : '{valeur}' (off/tout/une)"}
        common.call_service("repeat_set", eid, {"repeat": mode})
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
