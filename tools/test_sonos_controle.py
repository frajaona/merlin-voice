"""Offline tests for plugins/sonos_controle.py — fake HA, no network, no bot.

Run: venv/bin/python tools/test_sonos_controle.py

Monkeypatches the plugin's `_ha_request` with a recorder serving canned
states, in the spirit of test_dashboard_api.py (duck-typed fakes).
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "merlin_plugins.sonos_controle", REPO / "plugins" / "sonos_controle.py"
)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

SONOS_IDS = ["media_player.salon", "media_player.cuisine", "media_player.chambre_des_enfants"]


def make_states(salon="playing", cuisine="idle", chambre="idle", vol=0.30):
    def st(eid, name, state, extra=None):
        attrs = {"friendly_name": name, "volume_level": vol}
        attrs.update(extra or {})
        return {"entity_id": eid, "state": state, "attributes": attrs}

    return [
        st("media_player.salon", "Salon", salon,
           {"media_artist": "Daft Punk", "media_title": "Voyager",
            "group_members": ["media_player.salon"]}),
        st("media_player.cuisine", "Cuisine", cuisine),
        st("media_player.chambre_des_enfants", "Chambre des enfants", chambre),
        # Non-Sonos media_player: must be filtered out via the template list.
        st("media_player.tv_salon", "TV Salon", "off"),
    ]


class FakeHA:
    def __init__(self, states, template_fails=False):
        self.states = states
        self.template_fails = template_fails
        self.calls = []  # (service, payload)

    def request(self, method, path, payload=None):
        if path == "/api/template":
            if self.template_fails:
                raise RuntimeError("HA /api/template: HTTP 404")
            return json.dumps(SONOS_IDS)
        if path == "/api/states":
            return self.states
        if path.startswith("/api/services/media_player/"):
            self.calls.append((path.rsplit("/", 1)[1], payload))
            return []
        raise AssertionError(f"unexpected path {path}")


class FakeParams:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.results = []

    async def result_callback(self, result):
        self.results.append(result)


def run(fake, **arguments):
    sc._entity_cache = None  # no cross-test cache
    sc._ha_request = fake.request
    params = FakeParams(**arguments)
    asyncio.run(sc.handler(params))
    assert len(params.results) == 1, "result_callback must fire exactly once"
    return params.results[0]


def test_room_matching_and_pause():
    fake = FakeHA(make_states())
    # Article + accent-insensitive fuzzy match on friendly names.
    r = run(fake, action="pause", piece="la cuisine")
    assert r == {"ok": "pause dans Cuisine"}, r
    assert fake.calls == [("media_pause", {"entity_id": "media_player.cuisine"})]
    r = run(fake, action="suivant", piece="chambre des enfants")
    assert fake.calls[-1][0] == "media_next_track"
    assert fake.calls[-1][1]["entity_id"] == "media_player.chambre_des_enfants"
    print("ok: room matching (articles/accents) + pause/suivant")


def test_default_room_single_playing():
    fake = FakeHA(make_states())  # only Salon is playing
    r = run(fake, action="pause")
    assert r == {"ok": "pause dans Salon"}, r
    print("ok: implicit room = the only one playing")


def test_ambiguity_refuses_to_act():
    fake = FakeHA(make_states(cuisine="playing"))  # two rooms playing
    r = run(fake, action="pause")
    assert "error" in r and "précise" in r["error"], r
    assert fake.calls == [], "must NOT call any service on ambiguity"
    r = run(fake, action="pause", piece="garage")
    assert "error" in r and "Salon" in r["error"], r  # lists available rooms
    assert fake.calls == []
    print("ok: ambiguity/unknown room -> no action, explicit error")


def test_volume():
    fake = FakeHA(make_states(vol=0.30))
    r = run(fake, action="volume", piece="salon", valeur="45")
    assert fake.calls[-1] == ("volume_set", {"entity_id": "media_player.salon", "volume_level": 0.45}), fake.calls
    r = run(fake, action="volume", piece="salon", valeur="+10")
    assert fake.calls[-1][1]["volume_level"] == 0.40, fake.calls
    r = run(fake, action="volume", piece="salon", valeur="moins")
    assert fake.calls[-1][1]["volume_level"] == 0.20, fake.calls
    r = run(fake, action="volume", piece="salon")  # read-only
    assert r == {"piece": "Salon", "volume": 30}, r
    r = run(fake, action="volume", piece="salon", valeur="beaucoup")
    assert "error" in r, r
    print("ok: volume absolute/relative/read/invalid")


def test_toggles():
    fake = FakeHA(make_states())
    run(fake, action="muet", piece="salon")
    assert fake.calls[-1] == ("volume_mute", {"entity_id": "media_player.salon", "is_volume_muted": True})
    run(fake, action="muet", piece="salon", valeur="off")
    assert fake.calls[-1][1]["is_volume_muted"] is False
    run(fake, action="aleatoire", piece="salon", valeur="on")
    assert fake.calls[-1] == ("shuffle_set", {"entity_id": "media_player.salon", "shuffle": True})
    run(fake, action="repetition", piece="salon", valeur="une")
    assert fake.calls[-1][1]["repeat"] == "one"
    run(fake, action="repetition", piece="salon", valeur="off")
    assert fake.calls[-1][1]["repeat"] == "off"
    print("ok: muet/aleatoire/repetition mapping")


def test_grouping():
    fake = FakeHA(make_states())
    r = run(fake, action="grouper", piece="cuisine", valeur="salon")
    assert fake.calls[-1] == ("join", {"entity_id": "media_player.salon",
                                       "group_members": ["media_player.cuisine"]}), fake.calls
    assert r["ok"] == "Cuisine rejoint Salon", r
    # No master given -> the only playing room (Salon) is the master.
    r = run(fake, action="grouper", piece="cuisine")
    assert fake.calls[-1][1]["entity_id"] == "media_player.salon"
    r = run(fake, action="grouper", piece="salon", valeur="salon")
    assert "error" in r, r
    r = run(fake, action="degrouper", piece="cuisine")
    assert fake.calls[-1] == ("unjoin", {"entity_id": "media_player.cuisine"})
    print("ok: grouper (explicit + implicit master) / degrouper")


def test_statut():
    fake = FakeHA(make_states())
    r = run(fake, action="statut")
    assert r == {"en_cours": [{"piece": "Salon", "etat": "playing",
                               "artiste": "Daft Punk", "titre": "Voyager",
                               "volume": 30}]}, r
    r = run(fake, action="statut", piece="cuisine")
    assert r["etat"] == "idle" and r["piece"] == "Cuisine", r
    fake_idle = FakeHA(make_states(salon="paused"))
    r = run(fake_idle, action="statut")
    assert r["statut"] == "rien ne joue" and "Cuisine" in r["pieces"], r
    print("ok: statut global / per-room / nothing playing")


def test_template_fallback_and_errors():
    # Template unavailable -> fall back to ALL media_players (TV included).
    fake = FakeHA(make_states(), template_fails=True)
    r = run(fake, action="statut")
    assert "en_cours" in r, r
    r = run(fake, action="pause", piece="tv salon")  # reachable only in fallback
    assert r == {"ok": "pause dans TV Salon"}, r

    class DownHA:
        def request(self, *a, **k):
            raise RuntimeError("Home Assistant injoignable (http://x): refused")

    r = run(DownHA(), action="pause", piece="cuisine")
    assert "error" in r and "injoignable" in r["error"], r
    print("ok: template fallback + HA down -> single error result")


if __name__ == "__main__":
    test_room_matching_and_pause()
    test_default_room_single_playing()
    test_ambiguity_refuses_to_act()
    test_volume()
    test_toggles()
    test_grouping()
    test_statut()
    test_template_fallback_and_errors()
    print("all sonos_controle tests passed")
