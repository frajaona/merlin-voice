"""request_feature — enregistre les capacités manquantes demandées par l'utilisateur.

Les demandes s'accumulent dans data/feature-requests.jsonl ; c'est la file
d'attente du futur atelier nocturne (génération d'un nouveau plugin par un
modèle cloud, revue humaine, activation).
"""
import datetime
import json
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

REQUESTS_FILE = Path(__file__).resolve().parent.parent / "data" / "feature-requests.jsonl"

SCHEMA = FunctionSchema(
    name="request_feature",
    description=(
        "À appeler quand l'utilisateur demande une action que tu ne sais pas encore "
        "faire (contrôler la maison, mettre un minuteur, jouer de la musique, gérer "
        "un agenda...). Enregistre la demande pour qu'un nouvel outil soit développé. "
        "N'annonce jamais une action comme effectuée sans outil pour la faire réellement."
    ),
    properties={
        "capability": {
            "type": "string",
            "description": "La capacité manquante, en une phrase (ex : 'mettre un minuteur').",
        },
        "user_request": {
            "type": "string",
            "description": "La demande originale de l'utilisateur, reformulée fidèlement.",
        },
    },
    required=["capability"],
)


async def handler(params: FunctionCallParams):
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "capability": params.arguments.get("capability", ""),
        "user_request": params.arguments.get("user_request", ""),
        "status": "pending",
    }
    logger.info(f"request_feature: [{entry['capability']}]")
    try:
        REQUESTS_FILE.parent.mkdir(exist_ok=True)
        with open(REQUESTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        await params.result_callback({
            "logged": True,
            "instruction": (
                "Dis honnêtement que tu ne sais pas encore faire ça, que la demande "
                "est notée, et propose de fabriquer cet outil maintenant (quelques "
                "minutes). N'appelle build_skill que si l'utilisateur accepte."
            ),
        })
    except Exception as e:
        logger.warning(f"request_feature failed: {e}")
        await params.result_callback({"error": str(e)})
