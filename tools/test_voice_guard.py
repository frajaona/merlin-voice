"""Offline tests for voice_guard: hallucination filters + household gate.

Run: venv/bin/python tools/test_voice_guard.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from voice_guard import (
    ENROLL_TARGET,
    GateCore,
    HouseholdProfiles,
    LastBotUtterance,
    looks_hallucinated,
    normalize_words,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def unit(seed):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def near(base, seed, noise=0.3):
    """Unit vector at cosine ≈ 1/sqrt(1+noise²) ≈ 0.96 from base."""
    rng = np.random.default_rng(seed)
    g = rng.standard_normal(512).astype(np.float32)
    g /= np.linalg.norm(g)
    v = base + noise * g
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_hallucination_filters():
    assert looks_hallucinated("Sous-titrage ST' 501")
    assert looks_hallucinated("Sous-titres réalisés para la communauté d'Amara.org")
    assert looks_hallucinated("Merci d'avoir regardé cette vidéo !")
    assert looks_hallucinated("...") == "vide"
    assert looks_hallucinated(
        "t'es pas qu'on peut t'es pas qu'on peut t'es pas qu'on peut t'es pas qu'on peut"
    ) == "boucle de repetition"
    assert looks_hallucinated("Quelle est la météo demain à Bordeaux ?") is None
    assert looks_hallucinated("Merci.") is None  # short real thanks passes
    assert normalize_words("Salut Merlin, ça va ?") == ["salut", "merlin", "ca", "va"]
    print("ok: hallucination filters")


def make_household(tmp):
    return HouseholdProfiles(
        root=Path(tmp) / "voices", pending_path=Path(tmp) / "voices" / ".enrolling"
    )


def make_core(household, clock, **kwargs):
    defaults = dict(
        speaker_gate=True, threshold=0.60, family_mode=False, require_wake=True,
        followup_secs=12.0, question_secs=30.0, now=clock,
    )
    defaults.update(kwargs)
    return GateCore(household, LastBotUtterance(), **defaults)


def enroll_voice(household, name, base, seed0):
    profile = household.get_or_create(name)
    for i in range(ENROLL_TARGET):
        profile.enroll(near(base, seed0 + i))


def test_owner_enrollment_flow():
    """Fresh install: first speaker auto-enrolls as owner via pending marker."""
    clock = FakeClock()
    fred = unit(1)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        assert household.pending_name() == "proprietaire"
        core = make_core(household, clock)

        ok, why = core.evaluate("Salut Merlin comment ça va", near(fred, 10), 2.0)
        assert ok and "inscription proprietaire" in why, why
        n = 1
        while household.pending_name():
            clock.t += 5
            ok, _ = core.evaluate("Merlin quelle est la météo demain", near(fred, 11 + n), 2.0)
            assert ok
            n += 1
        assert household.people["proprietaire"].complete
        print(f"ok: owner auto-enrollment ({n} utterances)")


def test_activator_binding():
    clock = FakeClock()
    fred, wife, stranger = unit(1), unit(2), unit(3)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()  # no auto-enroll; build profiles directly
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)
        core = make_core(household, clock)

        # Stranger can't activate, even with the wake word.
        ok, why = core.evaluate("Merlin quelle heure est-il", near(stranger, 1), 2.0)
        assert not ok and "voix inconnue" in why, why

        # Fred activates and is bound.
        ok, why = core.evaluate("Merlin quelle heure est-il", near(fred, 2), 2.0)
        assert ok and "éveil par fred" in why, why
        assert core.activator == "fred"

        # Wife is enrolled but not the activator -> dropped mid-exchange.
        clock.t += 3
        ok, why = core.evaluate("et il fait quel temps aujourd'hui", near(wife, 3), 2.0)
        assert not ok and "pas l'activateur" in why, why

        # Fred continues fine.
        clock.t += 2
        ok, why = core.evaluate("et demain est-ce qu'il pleut", near(fred, 4), 2.0)
        assert ok, why

        # Wife takes the mic with the wake word.
        clock.t += 2
        ok, why = core.evaluate("Merlin et pour moi quel temps", near(wife, 5), 2.0)
        assert ok and "nouvel activateur camille" in why, why
        assert core.activator == "camille"

        # Now Fred is the one dropped mid-exchange.
        clock.t += 2
        ok, why = core.evaluate("réponds-moi à moi d'abord", near(fred, 6), 2.0)
        assert not ok and "pas l'activateur" in why, why

        # Short utterances pass inside the exchange (unstable embeddings).
        clock.t += 2
        ok, why = core.evaluate("Oui.", near(stranger, 7), 0.6)
        assert ok and "court" in why, why

        # One-word reply with buffer-inflated duration: still unverifiable
        # (a real "Non." at 1.9s embedded at sim 0.08 vs its own speaker).
        clock.t += 2
        ok, why = core.evaluate("Non.", near(stranger, 9), 1.9)
        assert ok and "court" in why, why

        # Attention expires -> binding cleared; side talk dropped.
        clock.t += 40
        ok, why = core.evaluate("on mange quoi ce soir", near(wife, 8), 2.0)
        assert not ok and "hors attention" in why, why
        assert core.activator is None
        print("ok: activator binding (stranger blocked, mic passing, expiry)")


def test_family_mode_and_short_wake():
    clock = FakeClock()
    fred, wife = unit(1), unit(2)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)

        # Family mode: any enrolled voice accepted mid-exchange, no rebind needed.
        core = make_core(household, clock, family_mode=True)
        ok, _ = core.evaluate("Merlin on veut une recette de crêpes", near(fred, 1), 2.0)
        assert ok
        clock.t += 3
        ok, why = core.evaluate("avec du beurre salé s'il te plaît", near(wife, 2), 2.0)
        assert ok and "camille" in why, why

        # Short wake utterance: lenient identity bar, binds without anchor.
        core2 = make_core(household, clock)
        clock.t += 100  # attention closed
        ok, why = core2.evaluate("Merlin ?", near(fred, 3, noise=1.2), 0.8)  # sim ~0.6 but short
        assert ok and "court" in why, why
        assert core2.activator == "fred"

        # Fail-open: no embedding never locks anyone out.
        clock.t += 200
        ok, why = core2.evaluate("Merlin tu es là", None, 2.0)
        assert ok and "indisponible" in why, why
        print("ok: family mode, short wake, fail-open")


def test_embedding_separation():
    """Sanity-check the real model wiring (synthetic voices, not real speech)."""
    from voice_guard import compute_embedding

    def voice(f0, seed):
        rng2 = np.random.default_rng(seed)
        t = np.arange(48000) / 16000
        sig = sum(np.sin(2 * np.pi * f0 * k * t + rng2.uniform(0, 6)) / k for k in range(1, 6))
        sig += 0.05 * rng2.standard_normal(len(t))
        return (0.1 * sig / np.abs(sig).max()).astype(np.float32)

    a1 = compute_embedding(voice(120, 1))
    a2 = compute_embedding(voice(120, 2))
    b = compute_embedding(voice(240, 3))
    same, diff = float(a1 @ a2), float(a1 @ b)
    print(f"ok: embeddings wired (same-ish={same:.2f}, different={diff:.2f})")
    assert same > diff


if __name__ == "__main__":
    test_hallucination_filters()
    test_owner_enrollment_flow()
    test_activator_binding()
    test_family_mode_and_short_wake()
    test_embedding_separation()
    print("all voice_guard tests passed")
