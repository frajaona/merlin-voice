"""mettre_un_minuteur — permet de démarrer un minuteur."""
import asyncio
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

SCHEMA = FunctionSchema(
    name="mettre_un_minuteur",
    description=(
        "Permet de démarrer un minuteur ou une alarme courte. À utiliser "
        "lorsque l'utilisateur demande à être prévenu dans un certain délai."
    ),
    properties={
        "duration_seconds": {
            "type": "integer",
            "description": "La durée du minuteur en secondes.",
        }
    },
    required=["duration_seconds"],
)


async def _timer_task(duration: int, llm):
    """Tâche en arrière-plan qui attend la durée puis pousse une phrase."""
    await asyncio.sleep(duration)
    logger.info(f"Minuteur de {duration} secondes terminé.")
    await llm.push_frame(TTSSpeakFrame("C'est l'heure ! Le minuteur est terminé."))


async def handler(params: FunctionCallParams):
    try:
        duration = params.arguments.get("duration_seconds")
        if duration is None:
            await params.result_callback({"error": "La durée (duration_seconds) est requise."})
            return
            
        duration = int(duration)
        if duration <= 0:
            await params.result_callback({"error": "La durée doit être strictement positive."})
            return

        logger.info(f"mettre_un_minuteur: [{duration}s]")
        
        # On lance la tâche en arrière-plan sans bloquer la boucle d'événements
        asyncio.create_task(_timer_task(duration, params.llm))
        
        # On appelle le callback de résultat exactement une fois avec un dict
        await params.result_callback({"status": "minuteur lancé", "duration_seconds": duration})
    except Exception as e:
        logger.warning(f"mettre_un_minuteur failed: {e}")
        await params.result_callback({"error": f"impossible de démarrer le minuteur: {e}"})
