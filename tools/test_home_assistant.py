"""Offline tests for plugins/home_assistant.py — fake HA, no network, no bot.

Run: venv/bin/python tools/test_home_assistant.py

Same pattern as test_sonos_controle.py: the shared HA client's `ha_request`
is monkeypatched with a recorder serving canned states modeled on the real
house (2026-08-21 inventory: Hue lights + room groups, 17 scenes, no covers).
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "merlin_plugins.home_assistant", REPO / "plugins" / "home_assistant.py"
)
hap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hap)


def st(eid, name, state, brightness=None):
    attrs = {"friendly_name": name}
    if brightness is not None:
        attrs["brightness"] = brightness
    return {"entity_id": eid, "state": state, "attributes": attrs}


def make_states():
    return [
        st("light.cuisine", "Cuisine", "on", brightness=204),  # groupe Hue, 80 %
        st("light.chevet_fred", "Chevet Fred", "off"),
        st("light.chevet_camille", "Chevet camille", "off"),
        st("light.lampadaire", "Lampadaire", "off"),
        st("light.suspension_dressing", "Suspension Dressing", "off"),
        st("light.suspension_milieu", "Suspension milieu", "off"),
        # Must never be matched nor counted:
        st("light.suspension_1", "Suspension 1", "unavailable"),
        st("light.home_assistant_voice_098c6a_led_ring", "LED Ring", "on"),
        # Scenes (state is a timestamp/unknown in HA — irrelevant here):
        st("scene.dressing_lecture", "Dressing Lecture", "unknown"),
        st("scene.cuisine_lumineux", "Cuisine Lumineux", "unknown"),
        st("scene.dressing_lumineux", "Dressing Lumineux", "unknown"),
        st("scene.chambre_lumineux", "Chambre Lumineux", "unknown"),
        # Other domains must be ignored:
        st("switch.cuisine_crossfade", "Cuisine Crossfade", "off"),
        st("media_player.cuisine", "Cuisine", "idle"),
    ]


AVAILABLE = ["light.chevet_camille", "light.chevet_fred", "light.cuisine",
             "light.lampadaire", "light.suspension_dressing",
             "light.suspension_milieu"]


class FakeHA:
    def __init__(self, states, down=False):
        self.states = states
        self.down = down
        self.calls = []  # (path, payload)

    def request(self, method, path, payload=None):
        if self.down:
            raise RuntimeError("Home Assistant injoignable (http://x): boom")
        if path == "/api/states":
            return self.states
        if path.startswith("/api/services/"):
            self.calls.append((path.removeprefix("/api/services/"), payload))
            return []
        raise AssertionError(f"unexpected path {path}")


class FakeParams:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.results = []

    async def result_callback(self, result):
        self.results.append(result)


def run(fake, **arguments):
    hap.ha.ha_request = fake.request
    params = FakeParams(**arguments)
    asyncio.run(hap.handler(params))
    assert len(params.results) == 1, "result_callback must fire exactly once"
    return params.results[0]


def test_on_off_matching():
    fake = FakeHA(make_states())
    # Article + accent-insensitive match on friendly names.
    r = run(fake, action="allumer", cible="la cuisine")
    assert r == {"ok": "Cuisine allumée"}, r
    assert fake.calls == [("light/turn_on", {"entity_id": "light.cuisine"})]
    r = run(fake, action="eteindre", cible="lampadaire")
    assert r == {"ok": "Lampadaire éteinte"}, r
    assert fake.calls[-1] == ("light/turn_off", {"entity_id": "light.lampadaire"})
    # Unique substring: "chevet de fred" is not a name, "fred" is unique.
    r = run(fake, action="allumer", cible="fred")
    assert fake.calls[-1][1]["entity_id"] == "light.chevet_fred", fake.calls
    print("ok: allumer/eteindre + matching articles/accents/sous-chaîne")


def test_ambiguity_refuses_to_act():
    fake = FakeHA(make_states())
    r = run(fake, action="allumer", cible="suspension")
    assert "error" in r and r["candidats"] == [
        "Suspension Dressing", "Suspension milieu"], r
    assert fake.calls == [], "must NOT call any service on ambiguity"
    r = run(fake, action="allumer", cible="garage")
    assert "error" in r and "Cuisine" in r["lumieres"], r
    assert "Suspension 1" not in r["lumieres"], r  # unavailable hidden
    assert fake.calls == []
    print("ok: ambiguïté/inconnue -> aucune action, candidats explicites")


def test_all_lights():
    fake = FakeHA(make_states())
    r = run(fake, action="eteindre", cible="tout")
    assert r == {"ok": "6 lumières éteintes"}, r
    assert fake.calls == [("light/turn_off", {"entity_id": AVAILABLE})], fake.calls
    print("ok: 'tout' -> toutes les lumières joignables, LED/unavailable exclues")


def test_brightness():
    fake = FakeHA(make_states())
    r = run(fake, action="luminosite", cible="cuisine", valeur="40")
    assert fake.calls[-1] == ("light/turn_on",
                              {"entity_id": "light.cuisine", "brightness_pct": 40}), fake.calls
    r = run(fake, action="luminosite", cible="cuisine", valeur="moins")
    assert fake.calls[-1][1]["brightness_pct"] == 60, fake.calls  # 80 - 20
    r = run(fake, action="allumer", cible="cuisine", valeur="+10")
    assert fake.calls[-1][1]["brightness_pct"] == 90, fake.calls
    r = run(fake, action="luminosite", cible="cuisine", valeur="0")
    assert fake.calls[-1] == ("light/turn_off", {"entity_id": "light.cuisine"})
    r = run(fake, action="luminosite", cible="cuisine", valeur="beaucoup")
    assert "error" in r, r
    r = run(fake, action="luminosite", cible="cuisine")
    assert "error" in r, r
    print("ok: luminosité absolue/relative/0->off/invalide")


def test_scenes():
    fake = FakeHA(make_states())
    # Token order does not matter: "lecture dressing" == "Dressing Lecture".
    r = run(fake, action="scene", cible="lecture dressing")
    assert r == {"ok": "scène Dressing Lecture activée"}, r
    assert fake.calls == [("scene/turn_on", {"entity_id": "scene.dressing_lecture"})]
    r = run(fake, action="scene", cible="lumineux")
    assert "error" in r and len(r["candidats"]) == 3, r
    assert len(fake.calls) == 1, "ambiguous scene must not act"
    r = run(fake, action="scene", cible="fiesta")
    assert "error" in r and "Cuisine Lumineux" in r["scenes"], r
    print("ok: scènes (ordre des mots, ambiguïté, inconnue)")


def test_status():
    fake = FakeHA(make_states())
    r = run(fake, action="statut")
    assert r == {"allumees": [{"lumiere": "Cuisine", "luminosite": 80}]}, r
    r = run(fake, action="statut", cible="chevet camille")
    assert r == {"lumiere": "Chevet camille", "etat": "off"}, r
    fake2 = FakeHA([s for s in make_states() if s["entity_id"] != "light.cuisine"])
    r = run(fake2, action="statut")
    assert r == {"statut": "toutes les lumières sont éteintes"}, r
    # Light without a brightness attribute (on/off only): no bogus '0 %'.
    fake3 = FakeHA(make_states() + [st("light.plan_travail", "Plan de travail", "on")])
    r = run(fake3, action="statut")
    assert {"lumiere": "Plan de travail"} in r["allumees"], r
    print("ok: statut global + par lumière + sans luminosité exposée")


def test_ha_down_and_bad_action():
    r = run(FakeHA([], down=True), action="allumer", cible="cuisine")
    assert "error" in r and "injoignable" in r["error"], r
    r = run(FakeHA(make_states()), action="danser")
    assert r == {"error": "action inconnue : danser"}, r
    r = run(FakeHA(make_states()), action="allumer")
    assert "error" in r and "lumieres" in r, r
    print("ok: HA injoignable / action inconnue / cible manquante -> erreur propre")


if __name__ == "__main__":
    test_on_off_matching()
    test_ambiguity_refuses_to_act()
    test_all_lights()
    test_brightness()
    test_scenes()
    test_status()
    test_ha_down_and_bad_action()
    print("\nall home_assistant tests passed")
