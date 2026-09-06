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
    # Music-into-mic variant (2026-08-19): same word 3+ times = hallucination;
    # a doubled word stays valid (people really say "oui oui" / "merci merci").
    assert looks_hallucinated("Merci. Merci. Merci.") == "boucle de repetition"
    assert looks_hallucinated("Merci. Merci. Merci. Merci.") == "boucle de repetition"
    assert looks_hallucinated("Merci. Merci.") is None
    assert looks_hallucinated("Oui oui.") is None
    assert normalize_words("Salut Olympia, ça va ?") == ["salut", "olympia", "ca", "va"]
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

        ok, why = core.evaluate("Salut Olympia comment ça va", near(fred, 10), 2.0)
        assert ok and "inscription proprietaire" in why, why
        assert core.last_speaker == "proprietaire"
        n = 1
        while household.pending_name():
            clock.t += 5
            ok, _ = core.evaluate("Olympia quelle est la météo demain", near(fred, 11 + n), 2.0)
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
        ok, why = core.evaluate("Olympia quelle heure est-il", near(stranger, 1), 2.0)
        assert not ok and "voix inconnue" in why, why

        # Fred activates and is bound.
        ok, why = core.evaluate("Olympia quelle heure est-il", near(fred, 2), 2.0)
        assert ok and "éveil par fred" in why, why
        assert core.activator == "fred"
        assert core.last_speaker == "fred"

        # Wife is enrolled but not the activator -> dropped mid-exchange.
        clock.t += 3
        ok, why = core.evaluate("et il fait quel temps aujourd'hui", near(wife, 3), 2.0)
        assert not ok and "pas l'activateur" in why, why
        assert core.last_speaker is None  # rejected turns are never attributed

        # Fred continues fine.
        clock.t += 2
        ok, why = core.evaluate("et demain est-ce qu'il pleut", near(fred, 4), 2.0)
        assert ok, why

        # Wife takes the mic with the wake word.
        clock.t += 2
        ok, why = core.evaluate("Olympia et pour moi quel temps", near(wife, 5), 2.0)
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
        assert core.last_speaker == "camille"  # credited to the activator

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
        ok, _ = core.evaluate("Olympia on veut une recette de crêpes", near(fred, 1), 2.0)
        assert ok
        clock.t += 3
        ok, why = core.evaluate("avec du beurre salé s'il te plaît", near(wife, 2), 2.0)
        assert ok and "camille" in why, why
        assert core.last_speaker == "camille"  # who spoke, not the activator

        # Short wake utterance: lenient identity bar, binds without anchor.
        core2 = make_core(household, clock)
        clock.t += 100  # attention closed
        ok, why = core2.evaluate("Olympia ?", near(fred, 3, noise=1.2), 0.8)  # sim ~0.6 but short
        assert ok and "court" in why, why
        assert core2.activator == "fred"

        # Fail-open: no embedding never locks anyone out.
        clock.t += 200
        ok, why = core2.evaluate("Olympia tu es là", None, 2.0)
        assert ok and "indisponible" in why, why
        assert core2.last_speaker is None  # fail-open passes, but no attribution
        print("ok: family mode, short wake, fail-open")


def test_stop_and_privacy_hold():
    clock = FakeClock()
    fred, wife, stranger = unit(1), unit(2), unit(3)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)
        core = make_core(household, clock)

        # Open an exchange, then stop it mid-conversation.
        ok, _ = core.evaluate("Olympia quelle heure est-il", near(fred, 1), 2.0)
        assert ok
        clock.t += 2
        ok, why = core.evaluate("Olympia chut", near(fred, 2), 1.0)
        assert not ok and why == "stop → mode privé", why
        assert core.on_hold and core.activator is None

        # Under hold: nothing passes, nothing is attributed — even the
        # activator's own voice, even with the wake word but a short bar.
        clock.t += 2
        ok, why = core.evaluate("bonjour entre donc je t'en prie", near(fred, 3), 2.5)
        assert not ok and why == "privé", why
        assert core.last_speaker is None
        clock.t += 2
        ok, why = core.evaluate("Olympia ?", near(fred, 4), 0.6)  # short: no leniency here
        assert not ok and why == "privé", why
        # Unknown voice can't lift it (guest saying "Olympia" at the demo).
        clock.t += 2
        ok, why = core.evaluate("Olympia tu m'entends", near(stranger, 5), 2.0)
        assert not ok and why == "privé", why
        # Embedding failure fails CLOSED under hold (inverse of the wake bias).
        clock.t += 2
        ok, why = core.evaluate("Olympia tu es là", None, 2.0)
        assert not ok and why == "privé", why
        assert core.on_hold

        # A verified enrolled wake sentence lifts the hold and activates.
        clock.t += 2
        ok, why = core.evaluate("Olympia on peut reprendre maintenant", near(fred, 6), 2.5)
        assert ok and "fin du mode privé" in why, why
        assert not core.on_hold and core.activator == "fred" and core.last_speaker == "fred"

        # Stop works from ANY voice (privacy asymmetry), in either word order,
        # and "Olympia stop-kill" matches via the "stop" token.
        clock.t += 2
        ok, why = core.evaluate("Chut Olympia", near(stranger, 7), 1.2)
        assert not ok and why == "stop → mode privé", why
        assert core.on_hold
        clock.t += 2
        ok, _ = core.evaluate("Olympia on peut reprendre maintenant", near(wife, 8), 2.5)
        assert ok
        clock.t += 2
        ok, why = core.evaluate("Olympia stop-kill", near(wife, 9), 1.5)
        assert not ok and why == "stop → mode privé", why

        # A lone "chut" (no wake word around) never stops — someone shushing
        # a kid mid-exchange must not kill the session.
        clock.t += 2
        ok, _ = core.evaluate("Olympia on peut reprendre maintenant", near(fred, 10), 2.5)
        assert ok
        clock.t += 2
        ok, why = core.evaluate("chut les enfants on se calme", near(fred, 11), 2.0)
        assert ok, why  # normal mid-exchange turn, not a stop
        assert not core.on_hold

        # Raw-audio path: enter_hold() called directly (listener callback).
        core.enter_hold()
        assert core.on_hold and core.activator is None
        clock.t += 2
        ok, why = core.evaluate("il fait beau aujourd'hui", near(fred, 12), 2.0)
        assert not ok and why == "privé", why
        print("ok: stop phrase + privacy hold (any voice stops, verified wake lifts)")


def test_stop_activator_only():
    """MERLIN_STOP_ACTIVATOR_ONLY=1: mid-exchange, only the activator stops."""
    clock = FakeClock()
    fred, wife, stranger = unit(1), unit(2), unit(3)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)
        core = make_core(household, clock, stop_activator_only=True)

        # No activator bound: nothing to hijack, anyone may stop.
        ok, why = core.evaluate("Olympia chut", near(stranger, 1), 1.2)
        assert not ok and why == "stop → mode privé", why
        assert core.on_hold

        clock.t += 2
        ok, _ = core.evaluate("Olympia on peut reprendre maintenant", near(fred, 2), 2.5)
        assert ok and core.activator == "fred"

        # Enrolled but not the activator -> stop refused, session intact.
        clock.t += 2
        ok, why = core.evaluate("Olympia chut", near(wife, 3), 1.2)
        assert not ok and why == "stop refusé (pas l'activateur)", why
        assert not core.on_hold and core.activator == "fred"
        clock.t += 2
        ok, why = core.evaluate("Chut Olympia", near(stranger, 4), 1.2)
        assert not ok and "refusé" in why and not core.on_hold, why

        # The raw-audio channel has no voice identity: inert in this mode
        # (it only cut the TTS; the transcript pass decides the hold).
        core.raw_stop()
        assert not core.on_hold

        # Embedding failure stops anyway: a missed stop is the worse failure.
        clock.t += 2
        ok, why = core.evaluate("Olympia chut", None, 1.2)
        assert not ok and why == "stop → mode privé" and core.on_hold, why

        # The activator's own stop works (lenient bar on a short phrase).
        clock.t += 2
        ok, _ = core.evaluate("Olympia on peut reprendre maintenant", near(fred, 5), 2.5)
        assert ok
        clock.t += 2
        ok, why = core.evaluate("Olympia chut", near(fred, 6), 1.2)
        assert not ok and why == "stop → mode privé" and core.on_hold, why

        # Default mode: raw_stop holds immediately.
        core2 = make_core(household, clock)
        core2.raw_stop()
        assert core2.on_hold
        print("ok: activator-only stop (others refused, raw channel deferred)")


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


def test_lift_hold_http():
    """GateCore.lift_hold(): sortie HTTP du mode privé (bouton 🔔 —
    incident 19/08 : hold impossible à lever à la voix en mauvaise
    acoustique, aucune issue à part reconnecter)."""
    clock = FakeClock()
    fred = unit(1)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        core = make_core(household, clock)
        ok, _ = core.evaluate("Olympia quelle heure est-il", near(fred, 2), 2.0)
        assert ok
        core.enter_hold()
        assert core.on_hold
        # Sous privé, un éveil court reste refusé (barre pleine, par design).
        clock.t += 2
        ok, why = core.evaluate("Olympia tu es là", near(fred, 3), 0.6)
        assert not ok, why
        core.lift_hold()
        assert not core.on_hold
        # L'éveil normal remarche, leniency courte incluse.
        clock.t += 2
        ok, why = core.evaluate("Olympia ?", near(fred, 4), 0.6)
        assert ok and "court" in why, why
        core.lift_hold()  # idempotent hors hold
    print("ok: lift_hold (HTTP) sort du mode privé, éveil normal restauré")


def test_polite_closer():
    """Thanks/farewells close the exchange instead of being answered
    (incident 2026-08-19: a bystander's 'Merci.' was leniency-credited to
    the activator and every 'De rien !' re-armed the window)."""
    from voice_guard import is_polite_closer

    # Phrase classification: gratitude/farewell close, answers don't.
    for text in ("Merci.", "Merci Olympia !", "Merci beaucoup, c'est gentil.",
                 "Au revoir.", "À bientôt Olympia.", "OK merci."):
        assert is_polite_closer(normalize_words(text)), text
    for text in ("Oui.", "Non.", "D'accord.", "OK.", "C'est bon.",
                 "Merci de me dire l'heure.", "Merci, merci, Daniel."):
        assert not is_polite_closer(normalize_words(text)), text

    clock = FakeClock()
    fred, wife = unit(1), unit(2)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        core = make_core(household, clock)

        ok, why = core.evaluate("Olympia quelle heure est-il", near(fred, 2), 2.0)
        assert ok and core.activator == "fred", why

        # Bystander's short 'Merci.' -> IGNORED: no 'De rien !', never
        # credited to the activator (old leniency path), and the exchange
        # stays open — only the activator may close (Fred, 2026-08-19).
        clock.t += 3
        ok, why = core.evaluate("Merci.", near(wife, 3), 0.6)
        assert not ok and "ignorée" in why, why
        assert core.activator == "fred" and core.last_speaker is None

        # Exchange still open: activator continues without a wake word.
        clock.t += 2
        ok, why = core.evaluate("et demain est-ce qu'il pleut", near(fred, 4), 2.0)
        assert ok, why

        # Unverifiable closer (no embedding) is ignored too: closing is an
        # action, prefer not acting.
        clock.t += 2
        ok, why = core.evaluate("Merci.", None, 0.6)
        assert not ok and "ignorée" in why, why
        assert core.activator == "fred"

        # Short answers to a bot question still pass the leniency.
        clock.t += 3
        ok, why = core.evaluate("Oui.", near(wife, 6), 0.6)
        assert ok and "court" in why, why

        # The activator's own thanks closes (lenient bar: profile/anchor).
        clock.t += 2
        ok, why = core.evaluate("Merci Olympia.", near(fred, 7), 0.8)
        assert not ok and "échange fermé" in why, why
        assert core.activator is None

        # Really closed: the next sentence needs a wake again.
        clock.t += 2
        ok, why = core.evaluate("et après-demain il pleut", near(fred, 8), 2.0)
        assert not ok and "hors attention" in why, why
    print("ok: polite closers — activator closes, others ignored, answers pass")


def test_topup_rolling_cap_and_stale_marker():
    """Top-up on a full profile (incident 2026-08-21): the rolling PROFILE_MAX
    cap keeps count at 24, so an absolute target (32) was never reached and
    the marker stayed open — enrolling and answering near-any voice."""
    import os
    import time as _time

    from voice_guard import PROFILE_MAX

    clock = FakeClock()
    fred = unit(1)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        profile = household.get_or_create("fred")
        for i in range(PROFILE_MAX):
            profile.enroll(near(fred, 100 + i))
        assert profile.count == PROFILE_MAX

        # Top-up as voice_profile.py opens it: absolute target beyond the cap.
        household.start_enrollment("fred", target=PROFILE_MAX + 8)
        core = make_core(household, clock)
        n = 0
        while household.pending_name():
            clock.t += 5
            ok, why = core.evaluate(
                "Olympia une phrase de top up assez longue", near(fred, 300 + n), 2.0)
            assert ok and "top-up" in why, why
            n += 1
            assert n <= 8, "top-up must complete after 8 enrolled utterances"
        assert n == 8 and profile.count == PROFILE_MAX

        # Progress survives a restart mid top-up (counter lives in the marker).
        household.start_enrollment("fred", target=PROFILE_MAX + 8)
        clock.t += 5
        ok, why = core.evaluate(
            "Olympia une phrase de top up assez longue", near(fred, 400), 2.0)
        assert ok and "top-up 1/8" in why, why
        household2 = make_household(tmp)  # bot restart
        core2 = make_core(household2, clock)
        n = 1
        while household2.pending_name():
            clock.t += 5
            ok, why = core2.evaluate(
                "Olympia une phrase de top up assez longue", near(fred, 400 + n), 2.0)
            assert ok, why
            n += 1
        assert n == 8, f"restart must not reset top-up progress (took {n})"

        # A stale marker expires without enrolling anyone.
        household.start_enrollment("fred", target=PROFILE_MAX + 8)
        marker = Path(tmp) / "voices" / ".enrolling"
        old = _time.time() - 4000
        os.utime(marker, (old, old))
        assert household.pending_name() is None
        assert not marker.exists()
    print("ok: top-up au cap (8 énoncés), reprise après restart, marqueur périmé")


def test_adaptation_guards():
    """Runaway 2026-08-22: a polluted profile absorbed family voices at
    >= ADAPT_SIM, getting more porous with each one. Adaptation must refuse
    ambiguous voices (another profile almost as close) and freeze entirely
    while an enrollment marker is open (voices mix there by construction)."""
    clock = FakeClock()
    fred, wife = unit(1), unit(2)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)
        core = make_core(household, clock)
        n0 = household.people["fred"].count

        # Unambiguous fred voice, above the floor: adapts.
        core._adapt("fred", 0.85, near(fred, 300))
        assert household.people["fred"].count == n0 + 1

        # A voice much closer to camille than the claimed sim: refused.
        core._adapt("fred", 0.85, near(wife, 301))
        assert household.people["fred"].count == n0 + 1

        # Below the floor: refused.
        from voice_guard import ADAPT_SIM
        core._adapt("fred", ADAPT_SIM - 0.01, near(fred, 302))
        assert household.people["fred"].count == n0 + 1

        # Enrollment open: frozen even on a perfect match; thaws on close.
        household.start_enrollment("marcel")
        core._adapt("fred", 0.90, near(fred, 303))
        assert household.people["fred"].count == n0 + 1
        household.finish_enrollment()
        core._adapt("fred", 0.90, near(fred, 303))
        assert household.people["fred"].count == n0 + 2
    print("ok: adaptation gardée — marge inter-profils, gel pendant inscription")


def test_ambiguous_attribution_drops():
    """Marge d'attribution (2026-08-22) : nommer est un acte — une voix qui
    score presque pareil sur deux profils n'est pas nommée, le tour tombe."""
    clock = FakeClock()
    fred, wife = unit(1), unit(2)
    with tempfile.TemporaryDirectory() as tmp:
        household = make_household(tmp)
        household.finish_enrollment()
        enroll_voice(household, "fred", fred, 100)
        enroll_voice(household, "camille", wife, 200)
        core = make_core(household, clock)

        # A voice exactly between the two profiles: above threshold on both,
        # margin ~0 -> ambiguous, dropped, neither activator nor speaker set.
        mixed = (fred + wife) / np.linalg.norm(fred + wife)
        ok, why = core.evaluate("Olympia quelle est la météo demain", mixed, 2.0)
        assert not ok and "ambiguë" in why, why
        assert core.activator is None and core.last_speaker is None

        # A clear voice still wakes normally.
        ok, why = core.evaluate("Olympia quelle est la météo demain", near(fred, 50), 2.0)
        assert ok and core.activator == "fred", why
    print("ok: attribution ambiguë -> drop (aucun nom posé), voix franche passe")


if __name__ == "__main__":
    test_hallucination_filters()
    test_owner_enrollment_flow()
    test_activator_binding()
    test_family_mode_and_short_wake()
    test_stop_and_privacy_hold()
    test_stop_activator_only()
    test_lift_hold_http()
    test_polite_closer()
    test_embedding_separation()
    test_topup_rolling_cap_and_stale_marker()
    test_adaptation_guards()
    test_ambiguous_attribution_drops()
    print("all voice_guard tests passed")
