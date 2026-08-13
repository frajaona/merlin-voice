"""approve_skill — activation vocale d'un outil fabriqué par l'atelier.

N'active que des candidats ayant passé les gates (présents dans
data/skill-ready.jsonl). Promotion = déplacement dans plugins/ + commit git
(trace d'audit), puis chargement à chaud dans la session en cours :
enregistrement du handler sur le service LLM live et mise à jour du
ToolsSchema du contexte — l'outil est utilisable dans la même conversation.
"""
import asyncio
import importlib.util
import re
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

REPO = Path(__file__).resolve().parent.parent

SCHEMA = FunctionSchema(
    name="approve_skill",
    description=(
        "Active un nouvel outil fabriqué par l'atelier et en attente d'approbation. "
        "N'appelle cet outil QUE si l'utilisateur confirme explicitement l'activation "
        "(par exemple « active-le », « oui, active le minuteur »). Avant de demander "
        "confirmation, décris brièvement ce que fait l'outil."
    ),
    properties={
        "slug": {
            "type": "string",
            "description": "L'identifiant de l'outil à activer (ex : mettre_un_minuteur).",
        },
    },
    required=["slug"],
)


async def handler(params: FunctionCallParams):
    slug = (params.arguments.get("slug") or "").strip()
    if not re.fullmatch(r"[a-z0-9_]+", slug):
        await params.result_callback({"error": f"identifiant invalide: {slug!r}"})
        return

    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from skill_admin import built_entry, promote

    # Only gate-passed candidates are voice-approvable.
    entry = built_entry(slug)
    if entry is None:
        await params.result_callback({
            "error": f"aucun candidat validé nommé {slug}",
            "instruction": "Dis que tu ne trouves pas d'outil validé portant ce nom.",
        })
        return

    logger.info(f"approve_skill: activating [{slug}] (built by {entry.get('worker')})")
    try:
        # Promote (file move + git commit) off the event loop.
        target = await asyncio.to_thread(promote, slug)

        # Hot-load into the running session.
        spec = importlib.util.spec_from_file_location(f"merlin_plugins.{slug}", target)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        params.llm.register_function(module.SCHEMA.name, module.handler)

        # Add the new tool to the live context so the very next inference sees it.
        current = params.context.tools
        schemas = list(getattr(current, "standard_tools", []) or [])
        if all(s.name != module.SCHEMA.name for s in schemas):
            schemas.append(module.SCHEMA)
        params.context.set_tools(ToolsSchema(standard_tools=schemas))

        await params.result_callback({
            "activated": slug,
            "instruction": (
                "Confirme que l'outil est activé et immédiatement utilisable "
                "dans cette conversation."
            ),
        })
    except Exception as e:
        logger.warning(f"approve_skill failed for {slug}: {e}")
        await params.result_callback({"error": f"activation échouée: {e}"})
