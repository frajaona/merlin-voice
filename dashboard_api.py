"""Dashboard backend: shared-token auth + workshop REST endpoints.

Auth model: one shared token, from MERLIN_TOKEN or data/auth-token
(auto-generated at first startup, chmod 600). Required as
`Authorization: Bearer <token>` on /api/offer and everything under
/api/workshop. The static page itself stays public — the token only guards
actions: starting a WebRTC session (GPU use) and activating generated code.
The token is never accepted in the URL (query strings end up in access logs).

This REST surface plus the RTVI messages emitted by the pipeline (see
docs/DECISIONS.md 2026-08-16) are the contract for any future richer
dashboard living alongside static/index.html.
"""
import asyncio
import datetime
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

import skill_admin
import workshop

REPO = Path(__file__).resolve().parent
TOKEN_PATH = REPO / "data" / "auth-token"

_SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")

_token: str | None = None


def get_token() -> str:
    global _token
    if _token is not None:
        return _token
    _token = os.getenv("MERLIN_TOKEN", "").strip()
    if not _token and TOKEN_PATH.exists():
        _token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not _token:
        _token = secrets.token_urlsafe(24)
        TOKEN_PATH.parent.mkdir(exist_ok=True)
        TOKEN_PATH.write_text(_token + "\n", encoding="utf-8")
        TOKEN_PATH.chmod(0o600)
        logger.info(f"dashboard token generated: {TOKEN_PATH}")
    return _token


async def require_token(request: Request):
    header = request.headers.get("authorization", "")
    supplied = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
    if not supplied or not secrets.compare_digest(supplied, get_token()):
        raise HTTPException(status_code=401, detail="token invalide ou absent")


router = APIRouter(prefix="/api/workshop", dependencies=[Depends(require_token)])


# ---------------------------------------------------------------------------
# Panic stop: POST /api/stop {"speaker": "<name>"} puts the session(s) whose
# current activator is that enrolled person on privacy hold and cuts any
# in-flight answer — "Merlin chut" over HTTP, scoped to one person's
# exchange. The token authenticates the household; the speaker scopes the
# effect (another member's open exchange is left alone).
# ---------------------------------------------------------------------------

# session_id -> {"core": GateCore, "rtvi": RTVI processor}. Duck-typed so the
# offline tests can register fakes; bot.py registers each pipeline at start.
_sessions: dict = {}

# Enrolled-person source of truth (one .npz per profile). Module var so the
# offline tests can point it at a temp dir — never at the real profiles.
VOICES_DIR = REPO / "data" / "voices"


def register_session(session_id: str, core, rtvi):
    _sessions[session_id] = {"core": core, "rtvi": rtvi}


def unregister_session(session_id: str):
    _sessions.pop(session_id, None)


def _enrolled_names() -> set:
    return {p.stem for p in VOICES_DIR.glob("*.npz")}


stop_router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _sonos_refresh() -> dict:
    """Re-scanne l'index NAS (cache quotidien de plugins/_sonos_common)."""
    from plugins import _sonos_common as sonos

    return sonos.refresh_library()


@stop_router.post("/sonos/refresh")
async def sonos_refresh():
    """Bouton « 🔄 NAS » du dashboard : re-scan manuel de la bibliothèque
    (décision 19/08 : pas de re-scan automatique sur échec de recherche)."""
    try:
        counts = await asyncio.to_thread(_sonos_refresh)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"refreshed": counts}


@stop_router.post("/resume")
async def resume_all():
    """Bouton « 🔔 » : lève le mode privé de TOUTES les sessions retenues.
    Pas de scope par personne : enter_hold délie l'activateur, il n'y a
    plus personne à qui scoper — et le token est l'autorité du foyer
    (même logique que /api/stop). Motivé par l'incident du 19/08 : hold
    impossible à lever à la voix (acoustique), /api/stop matchait 0
    session, seule issue = reconnexion."""
    resumed = []
    for sid, s in list(_sessions.items()):
        core = s["core"]
        if not getattr(core, "on_hold", False):
            continue
        core.lift_hold()
        try:
            await s["rtvi"].send_server_message({
                "event": "gate-decision",
                "accepted": False,
                "reason": "fin du mode privé (requête HTTP)",
                "speaker": None,
                "text": "(reprise manuelle)",
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception as e:
            logger.warning(f"resume: session {sid}: {e}")
        resumed.append(sid)
    logger.info(f"privacy hold lifted via HTTP on {len(resumed)} session(s)")
    return {"resumed": len(resumed)}


@stop_router.post("/stop")
async def stop_person(body: dict):
    speaker = str(body.get("speaker", "")).strip()
    if not speaker:
        raise HTTPException(status_code=400, detail="speaker requis")
    if speaker not in _enrolled_names():
        raise HTTPException(status_code=404, detail=f"personne inconnue : {speaker}")
    stopped = []
    for sid, s in list(_sessions.items()):
        if getattr(s["core"], "activator", None) != speaker:
            continue
        s["core"].enter_hold()
        try:
            await s["rtvi"].interrupt_bot()
            # Same shape as the VoiceGate's live gate-decision events so the
            # dashboard feed renders it with no extra client logic.
            await s["rtvi"].send_server_message({
                "event": "gate-decision",
                "accepted": False,
                "reason": "stop → mode privé (requête HTTP)",
                "speaker": None,
                "text": f"(stop manuel — {speaker})",
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception as e:  # session mid-teardown: the hold is already set
            logger.warning(f"stop: session {sid}: {e}")
        stopped.append(sid)
    logger.info(f"privacy hold via HTTP for '{speaker}' on {len(stopped)} session(s)")
    return {"stopped": len(stopped), "speaker": speaker}


def _active_plugins() -> list:
    return sorted(
        p.stem for p in (REPO / "plugins").glob("*.py") if not p.name.startswith("_")
    )


def _log_tail(n: int = 40) -> list:
    if not workshop.LOG_FILE.exists():
        return []
    return workshop.LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:]


@router.get("")
async def workshop_state():
    def read():
        return {
            "requests": workshop.load_requests(),
            "active": _active_plugins(),
            "log": _log_tail(),
        }

    return await asyncio.to_thread(read)


@router.post("/approve")
async def approve(body: dict):
    slug = str(body.get("slug", ""))
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug invalide")
    # Only candidates that passed the workshop gates (AST scan + smoke test)
    # have a skill-ready entry — same proof the voice/CLI approval paths require.
    if skill_admin.built_entry(slug) is None:
        raise HTTPException(status_code=404, detail=f"aucun candidat validé pour « {slug} »")
    try:
        target = await asyncio.to_thread(skill_admin.promote, slug)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"échec du commit git: {e}")
    logger.info(f"skill '{slug}' approved from the dashboard -> {target}")
    # Plugins are rescanned at the start of each conversation; an ongoing
    # session picks it up on the next connection.
    return {"ok": True, "plugin": target.name}


@router.post("/dismiss")
async def dismiss(body: dict):
    """Dismiss a request by its `ts` id: a pending build is cancelled, a
    failed one is cleared, a built candidate is rejected (its skill-ready
    entry is removed too, so it can't be activated by voice/CLI either).
    A build in progress can't be dismissed."""
    ts = str(body.get("ts", ""))

    def do_dismiss():
        requests = workshop.load_requests()
        entry = next((r for r in requests if r.get("ts") == ts), None)
        if entry is None:
            raise HTTPException(status_code=404, detail="demande introuvable")
        if entry.get("status") == "building":
            raise HTTPException(status_code=409, detail="construction en cours — indéfaussable")
        if entry.get("status") == "dismissed":
            return entry  # idempotent
        entry["status"] = "dismissed"
        entry["dismissed_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        workshop.save_requests(requests)
        slug = entry.get("slug")
        if slug and skill_admin.READY_FILE.exists():
            kept = [
                line for line in skill_admin.READY_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("slug") != slug
            ]
            skill_admin.READY_FILE.write_text(
                "".join(l + "\n" for l in kept), encoding="utf-8"
            )
        return entry

    entry = await asyncio.to_thread(do_dismiss)
    logger.info(f"workshop request dismissed from the dashboard: [{entry.get('capability')}]")
    return {"ok": True}
