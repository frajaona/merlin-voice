import asyncio
import urllib.request
import urllib.parse
import json

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

SCHEMA = FunctionSchema(
    name="voir_la_meteo_d",
    description=(
        "Permet de voir la météo d'un lieu donné, en incluant la date (par exemple demain)."
    ),
    properties={
        "lieu": {
            "type": "string",
            "description": "Le lieu ou la ville pour laquelle on veut la météo.",
        },
        "date": {
            "type": "string",
            "description": "La date ou le jour ciblé, par exemple 'demain', 'lundi', etc.",
        },
    },
    required=["lieu"],
)

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _resolve_day_offset(date_str: str) -> int | None:
    """Map a French date expression to a wttr.in forecast index (0-2)."""
    import datetime
    text = (date_str or "").strip().lower()
    if text in ("", "aujourd'hui", "aujourd hui", "maintenant", "ce soir", "cette nuit"):
        return 0
    if "après-demain" in text or "apres-demain" in text or "apres demain" in text:
        return 2
    if "demain" in text:
        return 1
    today = datetime.date.today()
    for i, jour in enumerate(_JOURS):
        if jour in text:
            delta = (i - today.weekday()) % 7
            return delta if delta <= 2 else None
    try:
        delta = (datetime.date.fromisoformat(text) - today).days
        return delta if 0 <= delta <= 2 else None
    except ValueError:
        return None


def _desc(block: dict) -> str:
    if "lang_fr" in block:
        return block.get("lang_fr", [{}])[0].get("value", "")
    return block.get("weatherDesc", [{}])[0].get("value", "")


def _fetch_weather(lieu: str, date: str) -> dict:
    offset = _resolve_day_offset(date)
    if offset is None:
        return {
            "lieu": lieu, "date": date,
            "erreur": "prévisions disponibles uniquement pour aujourd'hui, demain et après-demain",
        }
    try:
        lieu_encoded = urllib.parse.quote(lieu)
        url = f"https://wttr.in/{lieu_encoded}?format=j1&lang=fr"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))

        days = data.get("weather", [])
        day = days[offset] if len(days) > offset else {}
        result = {
            "lieu": lieu,
            "date_demandee": date,
            "date": day.get("date", ""),
            "temperature_min_C": day.get("mintempC", "?"),
            "temperature_max_C": day.get("maxtempC", "?"),
            "source": "wttr.in",
        }
        hourly = day.get("hourly", [])
        if hourly:
            midi = hourly[min(4, len(hourly) - 1)]  # ~12:00
            result["meteo"] = _desc(midi)
            result["risque_pluie_pct"] = midi.get("chanceofrain", "?")
        if offset == 0:
            current = data.get("current_condition", [{}])[0]
            result["meteo_actuelle"] = _desc(current)
            result["temperature_actuelle_C"] = current.get("temp_C", "?")
        return result
    except Exception as e:
        return {
            "lieu": lieu,
            "date": date,
            "meteo": "Non disponible",
            "erreur": str(e)
        }

async def handler(params: FunctionCallParams):
    lieu = params.arguments.get("lieu", "")
    date = params.arguments.get("date", "aujourd'hui")
    
    logger.info(f"voir_la_meteo_d: lieu={lieu}, date={date}")
    
    await params.llm.push_frame(TTSSpeakFrame("Je regarde ça."))
    
    try:
        result = await asyncio.to_thread(_fetch_weather, lieu, date)
        await params.result_callback(result)
    except Exception as e:
        logger.warning(f"voir_la_meteo_d failed: {e}")
        await params.result_callback({"error": f"Erreur inattendue: {e}"})
