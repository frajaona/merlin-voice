"""build_skill — lance l'atelier de fabrication pour une demande enregistrée.

Séparé de request_feature pour que la fabrication (qui consomme du quota de
modèle cloud) ne démarre jamais sans confirmation explicite de l'utilisateur.
"""
import json
import subprocess
import sys
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

REPO = Path(__file__).resolve().parent.parent
REQUESTS_FILE = REPO / "data" / "feature-requests.jsonl"

SCHEMA = FunctionSchema(
    name="build_skill",
    description=(
        "Lance la fabrication d'un outil pour une demande déjà enregistrée par "
        "request_feature. N'appelle cet outil QUE si l'utilisateur a explicitement "
        "confirmé vouloir lancer la fabrication. La fabrication prend quelques "
        "minutes et tourne en arrière-plan."
    ),
    properties={
        "capability": {
            "type": "string",
            "description": (
                "Quelques mots de la capacité à fabriquer, pour retrouver la demande "
                "(ex : 'minuteur'). Vide = la demande en attente la plus ancienne."
            ),
        },
    },
    required=[],
)


def _find_pending(capability: str) -> dict | None:
    if not REQUESTS_FILE.exists():
        return None
    entries = [json.loads(l) for l in REQUESTS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    pending = [e for e in entries if e.get("status") == "pending"]
    if capability:
        needle = capability.lower()
        matches = [e for e in pending if needle in e.get("capability", "").lower()
                   or e.get("capability", "").lower() in needle]
        if matches:
            return matches[0]
    return pending[0] if pending else None


async def handler(params: FunctionCallParams):
    capability = (params.arguments.get("capability") or "").strip()
    entry = _find_pending(capability)
    if entry is None:
        await params.result_callback({
            "error": "aucune demande en attente",
            "instruction": "Dis qu'il n'y a aucune demande en attente de fabrication.",
        })
        return

    logger.info(f"build_skill: starting workshop for [{entry.get('capability')}]")
    try:
        log_file = open(REPO / "data" / "workshop.log", "a")
        subprocess.Popen(
            [sys.executable, str(REPO / "workshop.py")],
            cwd=REPO, stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        await params.result_callback({
            "building": entry.get("capability"),
            "instruction": (
                "Dis que la fabrication est lancée, qu'elle prendra quelques minutes, "
                "et que l'utilisateur peut demander où en est la fabrication à tout moment."
            ),
        })
    except Exception as e:
        logger.warning(f"build_skill failed: {e}")
        await params.result_callback({"error": str(e)})
