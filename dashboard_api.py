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
