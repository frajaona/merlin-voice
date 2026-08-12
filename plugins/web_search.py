"""web_search — recherche internet via DuckDuckGo (ddgs), sans clé API."""
import asyncio

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

SCHEMA = FunctionSchema(
    name="web_search",
    description=(
        "Recherche sur internet des informations actuelles (météo, actualités, "
        "horaires, prix, faits récents). À utiliser dès qu'une réponse fiable "
        "nécessite des données à jour."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "La requête de recherche, formulée comme pour un moteur de recherche.",
        },
        "type": {
            "type": "string",
            "enum": ["general", "news"],
            "description": "'news' pour les actualités (résultats récents et datés), 'general' sinon.",
        },
    },
    required=["query"],
)


def _ddg_search(query: str, search_type: str = "general", max_results: int = 5) -> list:
    from ddgs import DDGS
    with DDGS() as ddgs:
        if search_type == "news":
            # News index, restricted to the last week, with per-result dates.
            return [
                {
                    "date": r.get("date", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "source": r.get("source", ""),
                }
                for r in ddgs.news(query, region="fr-fr", timelimit="w", max_results=max_results)
            ]
        return [
            {
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "url": r.get("href", ""),
            }
            for r in ddgs.text(query, region="fr-fr", max_results=max_results)
        ]


async def handler(params: FunctionCallParams):
    query = params.arguments.get("query", "")
    search_type = params.arguments.get("type", "general")
    logger.info(f"web_search ({search_type}): [{query}]")
    # Fill the dead air while the search runs.
    await params.llm.push_frame(TTSSpeakFrame("Je regarde ça."))
    try:
        results = await asyncio.to_thread(_ddg_search, query, search_type)
        if not results:
            await params.result_callback({"error": "aucun résultat"})
            return
        await params.result_callback({"results": results})
    except Exception as e:
        logger.warning(f"web_search failed: {e}")
        await params.result_callback({"error": f"recherche indisponible: {e}"})
