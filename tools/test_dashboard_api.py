"""Offline tests for dashboard_api: token auth + workshop endpoints.

Run: venv/bin/python tools/test_dashboard_api.py

Uses Starlette's TestClient on a minimal app (no pipecat import, no bot).
All file paths are redirected to a temp dir — never touches data/.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import dashboard_api
import skill_admin
import workshop


def make_client(tmp: Path, monkeypatched_promote):
    dashboard_api._token = "secret-test-token"
    workshop.REQUESTS_FILE = tmp / "feature-requests.jsonl"
    workshop.LOG_FILE = tmp / "workshop.log"
    skill_admin.READY_FILE = tmp / "skill-ready.jsonl"
    skill_admin.promote = monkeypatched_promote

    app = FastAPI()
    app.include_router(dashboard_api.router)

    @app.post("/api/offer", dependencies=[Depends(dashboard_api.require_token)])
    async def offer():
        return {"ok": True}

    return TestClient(app)


def auth(token="secret-test-token"):
    return {"Authorization": f"Bearer {token}"}


def test_auth():
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(Path(tmp), lambda slug: Path(f"plugins/{slug}.py"))
        # No token, wrong token, wrong scheme -> 401; right token -> 200
        assert client.post("/api/offer").status_code == 401
        assert client.post("/api/offer", headers=auth("wrong")).status_code == 401
        assert client.post("/api/offer", headers={"Authorization": "secret-test-token"}).status_code == 401
        assert client.post("/api/offer", headers=auth()).status_code == 200
        assert client.get("/api/workshop").status_code == 401
        assert client.get("/api/workshop", headers=auth()).status_code == 200
    print("ok: token auth")


def test_workshop_state():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = make_client(tmp, lambda slug: Path(f"plugins/{slug}.py"))
        workshop.REQUESTS_FILE.write_text(
            json.dumps({"capability": "test", "status": "built", "slug": "test_skill"}) + "\n",
            encoding="utf-8",
        )
        workshop.LOG_FILE.write_text("line1\nline2\n", encoding="utf-8")
        data = client.get("/api/workshop", headers=auth()).json()
        assert data["requests"][0]["slug"] == "test_skill"
        assert data["log"] == ["line1", "line2"]
        assert isinstance(data["active"], list) and "web_search" in data["active"]
    print("ok: workshop state")


def test_approve():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        promoted = []

        def fake_promote(slug):
            promoted.append(slug)
            return Path(f"plugins/{slug}.py")

        client = make_client(tmp, fake_promote)
        skill_admin.READY_FILE.write_text(
            json.dumps({"slug": "good_skill", "capability": "x"}) + "\n", encoding="utf-8"
        )
        # Bad slug syntax, unknown slug -> rejected before promote
        assert client.post("/api/workshop/approve", headers=auth(),
                           json={"slug": "../evil"}).status_code == 400
        assert client.post("/api/workshop/approve", headers=auth(),
                           json={"slug": "not_built"}).status_code == 404
        assert promoted == []
        # Gate-passed slug -> promoted
        resp = client.post("/api/workshop/approve", headers=auth(), json={"slug": "good_skill"})
        assert resp.status_code == 200 and resp.json()["ok"] is True
        assert promoted == ["good_skill"]
        # No token -> untouched
        assert client.post("/api/workshop/approve", json={"slug": "good_skill"}).status_code == 401
        assert promoted == ["good_skill"]
    print("ok: approve endpoint")


def test_dismiss():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = make_client(tmp, lambda slug: Path(f"plugins/{slug}.py"))
        workshop.save_requests([
            {"ts": "T1", "capability": "a", "status": "pending"},
            {"ts": "T2", "capability": "b", "status": "building"},
            {"ts": "T3", "capability": "c", "status": "built", "slug": "c_skill"},
        ])
        skill_admin.READY_FILE.write_text(
            json.dumps({"slug": "c_skill"}) + "\n" + json.dumps({"slug": "other"}) + "\n",
            encoding="utf-8",
        )
        # Unknown ts, in-progress build -> refused
        assert client.post("/api/workshop/dismiss", headers=auth(), json={"ts": "nope"}).status_code == 404
        assert client.post("/api/workshop/dismiss", headers=auth(), json={"ts": "T2"}).status_code == 409
        # Pending -> dismissed (and idempotent)
        assert client.post("/api/workshop/dismiss", headers=auth(), json={"ts": "T1"}).status_code == 200
        assert client.post("/api/workshop/dismiss", headers=auth(), json={"ts": "T1"}).status_code == 200
        # Built -> dismissed + its skill-ready entry revoked (other entries kept)
        assert client.post("/api/workshop/dismiss", headers=auth(), json={"ts": "T3"}).status_code == 200
        statuses = {r["ts"]: r["status"] for r in workshop.load_requests()}
        assert statuses == {"T1": "dismissed", "T2": "building", "T3": "dismissed"}
        ready = [json.loads(l)["slug"] for l in skill_admin.READY_FILE.read_text().splitlines()]
        assert ready == ["other"]
        # No token -> untouched
        assert client.post("/api/workshop/dismiss", json={"ts": "T2"}).status_code == 401
    print("ok: dismiss endpoint")


def test_stop_endpoint():
    """POST /api/stop: token-gated, scoped to one enrolled person's session."""

    class FakeCore:
        def __init__(self, activator):
            self.activator = activator
            self.held = False

        def enter_hold(self):
            self.held = True

    class FakeRtvi:
        def __init__(self):
            self.interrupted = False
            self.messages = []

        async def interrupt_bot(self):
            self.interrupted = True

        async def send_server_message(self, data):
            self.messages.append(data)

    dashboard_api._token = "secret-test-token"
    app = FastAPI()
    app.include_router(dashboard_api.stop_router)
    client = TestClient(app)

    with tempfile.TemporaryDirectory() as tmp:
        # Enrolled people = fake profile files in a TEMP dir (never data/).
        dashboard_api.VOICES_DIR = Path(tmp)
        (Path(tmp) / "fred.npz").touch()
        (Path(tmp) / "camille.npz").touch()

        fred = (FakeCore("fred"), FakeRtvi())
        camille = (FakeCore("camille"), FakeRtvi())
        idle = (FakeCore(None), FakeRtvi())
        for sid, (core, rtvi) in {"s1": fred, "s2": camille, "s3": idle}.items():
            dashboard_api.register_session(sid, core, rtvi)
        try:
            body = {"speaker": "fred"}
            assert client.post("/api/stop", json=body).status_code == 401
            assert not fred[0].held  # unauthenticated call must not touch the gate
            assert client.post("/api/stop", json={}, headers=auth()).status_code == 400
            assert client.post("/api/stop", json={"speaker": "intrus"}, headers=auth()).status_code == 404

            resp = client.post("/api/stop", json=body, headers=auth())
            assert resp.status_code == 200
            assert resp.json() == {"stopped": 1, "speaker": "fred"}
            assert fred[0].held and fred[1].interrupted
            assert not camille[0].held and not idle[0].held  # others untouched
            assert fred[1].messages[0]["event"] == "gate-decision"
            assert fred[1].messages[0]["accepted"] is False

            # Person enrolled but with no open exchange anywhere -> 0 stopped.
            (Path(tmp) / "leo.npz").touch()
            resp = client.post("/api/stop", json={"speaker": "leo"}, headers=auth())
            assert resp.json() == {"stopped": 0, "speaker": "leo"}
            assert not camille[0].held and not idle[0].held
        finally:
            for sid in ("s1", "s2", "s3"):
                dashboard_api.unregister_session(sid)
    print("ok: /api/stop endpoint (scoped per enrolled person)")


def test_sonos_refresh():
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(Path(tmp), lambda slug: Path(f"plugins/{slug}.py"))
        client.app.include_router(dashboard_api.stop_router)
        orig = dashboard_api._sonos_refresh
        try:
            dashboard_api._sonos_refresh = lambda: {
                "artistes": 229, "albums": 419, "titres": 1000, "playlists": 55}
            assert client.post("/api/sonos/refresh").status_code == 401
            resp = client.post("/api/sonos/refresh", headers=auth())
            assert resp.status_code == 200
            assert resp.json() == {"refreshed": {
                "artistes": 229, "albums": 419, "titres": 1000, "playlists": 55}}

            def boom():
                raise RuntimeError("aucune enceinte Sonos vue par Home Assistant")
            dashboard_api._sonos_refresh = boom
            resp = client.post("/api/sonos/refresh", headers=auth())
            assert resp.status_code == 502 and "enceinte" in resp.json()["detail"]
        finally:
            dashboard_api._sonos_refresh = orig
    print("ok: /api/sonos/refresh (token, comptes, 502 propre)")


if __name__ == "__main__":
    test_auth()
    test_workshop_state()
    test_approve()
    test_dismiss()
    test_stop_endpoint()
    test_sonos_refresh()
    print("all dashboard_api tests passed")
