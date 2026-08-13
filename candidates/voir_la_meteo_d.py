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

def _fetch_weather(lieu: str, date: str) -> dict:
    try:
        lieu_encoded = urllib.parse.quote(lieu)
        url = f"https://wttr.in/{lieu_encoded}?format=j1&lang=fr"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            current = data.get("current_condition", [{}])[0]
            meteo_desc = current.get("lang_fr", [{}])[0].get("value", "Inconnue") if "lang_fr" in current else current.get("weatherDesc", [{}])[0].get("value", "Inconnue")
            temp = current.get("temp_C", "?")
            
            return {
                "lieu": lieu,
                "date": date,
                "meteo": meteo_desc,
                "temperature_C": temp,
                "source": "wttr.in"
            }
    except Exception as e:
        return {
            "lieu": lieu,
            "date": date,
            "meteo": "Non disponible",
            "erreur": str(e)
        }

async def handler(params: FunctionCallParams):
    lieu = params.arguments.get("lieu", "")
    date = params.arguments.get("date", "demain")
    
    logger.info(f"voir_la_meteo_d: lieu={lieu}, date={date}")
    
    await params.llm.push_frame(TTSSpeakFrame("Je regarde ça."))
    
    try:
        result = await asyncio.to_thread(_fetch_weather, lieu, date)
        await params.result_callback(result)
    except Exception as e:
        logger.warning(f"voir_la_meteo_d failed: {e}")
        await params.result_callback({"error": f"Erreur inattendue: {e}"})
