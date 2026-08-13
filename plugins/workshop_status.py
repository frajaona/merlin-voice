"""workshop_status — état de l'atelier de fabrication d'outils."""
import datetime
import json
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

REPO = Path(__file__).resolve().parent.parent
REQUESTS_FILE = REPO / "data" / "feature-requests.jsonl"
STALE_BUILD_MINUTES = 20

SCHEMA = FunctionSchema(
    name="workshop_status",
    description=(
        "Donne l'état de la fabrication des outils : demandes en attente, "
        "fabrications en cours, outils terminés en attente d'activation, échecs. "
        "À utiliser quand l'utilisateur demande où en est la fabrication d'un outil."
    ),
    properties={},
    required=[],
)


def _minutes_since(ts: str) -> int | None:
    try:
        return int((datetime.datetime.now() - datetime.datetime.fromisoformat(ts)).total_seconds() // 60)
    except (ValueError, TypeError):
        return None


async def handler(params: FunctionCallParams):
    logger.info("workshop_status")
    if not REQUESTS_FILE.exists():
        await params.result_callback({"requests": []})
        return
    entries = [json.loads(l) for l in REQUESTS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    report = []
    for e in entries:
        status = e.get("status")
        item = {"capability": e.get("capability"), "status": status}
        if status == "pending":
            item["note"] = "en attente d'une confirmation pour lancer la fabrication"
        elif status == "building":
            age = _minutes_since(e.get("building_ts") or e.get("ts"))
            if age is not None and age > STALE_BUILD_MINUTES:
                item["note"] = f"fabrication démarrée il y a {age} min — probablement échouée, à relancer"
            else:
                item["note"] = f"fabrication en cours{f' depuis {age} min' if age is not None else ''}"
        elif status == "built":
            item["slug"] = e.get("slug")
            if (REPO / "plugins" / f"{e.get('slug')}.py").exists():
                item["status"] = "active"
                item["note"] = "déjà activé et utilisable"
            else:
                item["note"] = "terminé, en attente d'activation (approve_skill après confirmation)"
        elif status == "failed":
            item["note"] = "la fabrication a échoué"
        report.append(item)
    await params.result_callback({
        "requests": report,
        "instruction": "Résume l'état naturellement en une ou deux phrases par outil.",
    })
