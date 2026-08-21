"""Contrôle de la maison via Home Assistant (HA Yellow, REST).

Item de la revue du 13/08, décision d'architecture : docs/DECISIONS.md
2026-08-21. Périmètre = ce que le Yellow expose réellement : lumières
(domaine `light`, dont les groupes de pièce Hue : Cuisine, Chambre,
Dressing, Entrée) et scènes d'éclairage (domaine `scene`). Pas de volets ni
de thermostat dans HA à ce jour — à étendre quand les entités existeront.
Même principe de gate que sonos_controle : en cas d'ambiguïté on n'agit
PAS, on renvoie les candidats. Client REST partagé : plugins/_ha_common.py.

Env : voir _ha_common.py (MERLIN_HA_URL, MERLIN_HA_TOKEN).
"""

import asyncio
import difflib

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from plugins import _ha_common as ha

SCHEMA = FunctionSchema(
    name="home_assistant",
    description=(
        "Contrôle la maison : allumer ou éteindre les lumières, régler "
        "leur luminosité, activer une scène ou ambiance d'éclairage, et "
        "savoir quelles lumières sont allumées."
    ),
    properties={
        "action": {
            "type": "string",
            "enum": ["allumer", "eteindre", "luminosite", "scene", "statut"],
            "description": "L'action à effectuer.",
        },
        "cible": {
            "type": "string",
            "description": (
                "La lumière ou la pièce visée (ex : cuisine, chevet Fred, "
                "lampadaire), ou 'tout' pour toutes les lumières. Pour "
                "'scene' : le nom de la scène (ex : dressing lecture). "
                "Optionnel pour 'statut'."
            ),
        },
        "valeur": {
            "type": "string",
            "description": (
                "Luminosité en pourcentage : '40', '+20', '-20', 'plus' "
                "ou 'moins'. Utilisable avec 'luminosite' et 'allumer'."
            ),
        },
    },
    required=["action"],
)

# Lumières « appareil » à ne jamais proposer à la voix (LED du satellite HA).
_EXCLUDE_SUBSTR = ("home_assistant_voice",)
_ALL_WORDS = ("tout", "toutes", "toutes les lumieres", "toutes les lampes",
              "partout", "toute la maison", "maison")


def _pools() -> tuple[dict[str, dict], dict[str, dict]]:
    """(lumières, scènes) joignables : {entity_id: state_dict}."""
    lights, scenes = {}, {}
    for s in ha.ha_request("GET", "/api/states"):
        eid = s.get("entity_id", "")
        if eid.startswith("light."):
            if s.get("state") == "unavailable":
                continue
            if any(x in eid for x in _EXCLUDE_SUBSTR):
                continue
            lights[eid] = s
        elif eid.startswith("scene."):
            scenes[eid] = s
    return lights, scenes


def _match(cible: str, pool: dict[str, dict]):
    """(entity_id, None) si match franc, sinon (None, candidats triés).

    Franc = nom exact, mêmes mots dans un autre ordre, sous-chaîne unique,
    ou unique voisin difflib. Plusieurs candidats → on n'agit pas.
    """
    names = {ha.norm(ha.friendly(s)): eid for eid, s in pool.items()}
    want = ha.norm(cible)
    if want in names:
        return names[want], None
    tokens = {" ".join(sorted(n.split())): eid for n, eid in names.items()}
    if " ".join(sorted(want.split())) in tokens:
        return tokens[" ".join(sorted(want.split()))], None
    subs = [n for n in names if want in n or n in want]
    if len(subs) == 1:
        return names[subs[0]], None
    if not subs:
        subs = difflib.get_close_matches(want, list(names), n=3, cutoff=0.75)
        if len(subs) == 1:
            return names[subs[0]], None
    return None, sorted(ha.friendly(pool[names[n]]) for n in subs)


def _names(pool: dict[str, dict]) -> list[str]:
    return sorted(ha.friendly(s) for s in pool.values())


def _brightness_pct(state: dict) -> int | None:
    """% de luminosité, None si la lumière ne l'expose pas (on/off simple)."""
    b = state.get("attributes", {}).get("brightness")
    return None if b is None else round(b / 255 * 100)


def _parse_pct(valeur: str, current: int):
    v = ha.norm(valeur).replace("%", "").replace("pour cent", "").strip()
    if v in ("plus", "plus fort", "monte", "augmente"):
        return min(100, current + 20)
    if v in ("moins", "baisse", "diminue"):
        return max(0, current - 20)
    try:
        n = int(v.replace(" ", ""))
    except ValueError:
        return None
    if v.startswith(("+", "-")):
        return max(0, min(100, current + n))
    return max(0, min(100, n))


def _turn(eids: list[str], on: bool, pct: int | None = None):
    payload: dict = {"entity_id": eids if len(eids) > 1 else eids[0]}
    if on and pct is not None:
        payload["brightness_pct"] = pct
    ha.ha_request(
        "POST", f"/api/services/light/turn_{'on' if on else 'off'}", payload
    )


def _run(action: str, cible: str, valeur: str) -> dict:
    """Cœur synchrone (exécuté via asyncio.to_thread)."""
    lights, scenes = _pools()

    if action == "scene":
        if not cible:
            return {"error": "précise la scène", "scenes": _names(scenes)}
        eid, candidats = _match(cible, scenes)
        if not eid:
            if candidats:
                return {"error": f"scène '{cible}' ambiguë", "candidats": candidats}
            return {"error": f"scène '{cible}' inconnue", "scenes": _names(scenes)}
        ha.ha_request("POST", "/api/services/scene/turn_on", {"entity_id": eid})
        return {"ok": f"scène {ha.friendly(scenes[eid])} activée"}

    if not lights:
        return {"error": "aucune lumière vue par Home Assistant"}

    if action == "statut":
        if cible and ha.norm(cible) not in _ALL_WORDS:
            eid, candidats = _match(cible, lights)
            if not eid:
                if candidats:
                    return {"error": f"'{cible}' est ambigu", "candidats": candidats}
                return {"error": f"lumière '{cible}' inconnue",
                        "lumieres": _names(lights)}
            s = lights[eid]
            out = {"lumiere": ha.friendly(s), "etat": s.get("state")}
            if s.get("state") == "on" and _brightness_pct(s) is not None:
                out["luminosite"] = _brightness_pct(s)
            return out
        allumees = [
            {"lumiere": ha.friendly(s)}
            | ({"luminosite": p} if (p := _brightness_pct(s)) is not None else {})
            for s in lights.values() if s.get("state") == "on"
        ]
        if not allumees:
            return {"statut": "toutes les lumières sont éteintes"}
        return {"allumees": allumees}

    if action in ("allumer", "eteindre", "luminosite"):
        on = action != "eteindre"
        if not cible:
            return {"error": "précise la lumière ou la pièce",
                    "lumieres": _names(lights)}
        if action != "luminosite" and ha.norm(cible) in _ALL_WORDS:
            _turn(sorted(lights), on)
            return {"ok": f"{len(lights)} lumières "
                          f"{'allumées' if on else 'éteintes'}"}
        eid, candidats = _match(cible, lights)
        if not eid:
            if candidats:
                return {"error": f"'{cible}' est ambigu", "candidats": candidats}
            return {"error": f"lumière '{cible}' inconnue",
                    "lumieres": _names(lights)}
        nom = ha.friendly(lights[eid])
        pct = None
        if valeur:
            pct = _parse_pct(valeur, _brightness_pct(lights[eid]) or 0)
            if pct is None:
                return {"error": f"luminosité incomprise : '{valeur}'"}
        if action == "luminosite" and pct is None:
            return {"error": "précise la luminosité (ex : 40, plus, moins)"}
        if action == "eteindre" or pct == 0:
            _turn([eid], False)
            return {"ok": f"{nom} éteinte"}
        _turn([eid], True, pct)
        return {"ok": f"{nom} allumée" + (f" à {pct}" if pct is not None else "")}

    return {"error": f"action inconnue : {action}"}


async def handler(params: FunctionCallParams):
    action = (params.arguments.get("action") or "").strip().lower()
    cible = (params.arguments.get("cible") or "").strip()
    valeur = (params.arguments.get("valeur") or "").strip()
    logger.info(f"home_assistant: action={action} cible={cible!r} valeur={valeur!r}")
    try:
        result = await asyncio.to_thread(_run, action, cible, valeur)
    except Exception as e:
        logger.warning(f"home_assistant failed: {e}")
        result = {"error": str(e)}
    await params.result_callback(result)
