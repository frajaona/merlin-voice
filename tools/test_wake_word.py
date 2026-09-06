"""End-to-end test of the raw-audio wake-word engine.

Streams synthesized French audio in 20ms chunks (like real WebRTC input)
through the WakeWordDetector and checks WakeState fires — including the case
that motivated the engine: Whisper mangling the wake word so the transcript
channel misses it. Also verifies GateCore honors the raw wake channel.

Run: venv/bin/python tools/test_wake_word.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from wake_word import StopState, WakeState, WakeWordDetector, is_stop_text, is_wake_text


def synth_batch():
    from kokoro_onnx import Kokoro

    cache = Path.home() / ".cache" / "kokoro-onnx"
    kokoro = Kokoro(str(cache / "kokoro-v1.0.onnx"), str(cache / "voices-v1.0.bin"))

    def synth(text, voice="ff_siwis", speed=1.0):
        s, sr = kokoro.create(text, voice=voice, speed=speed, lang="fr-fr")
        t = int(len(s) * 16000 / sr)
        f = np.interp(np.linspace(0, len(s) - 1, t), np.arange(len(s)), s)
        return (np.clip(f, -1, 1) * 32767).astype(np.int16)

    return synth


def test_matcher():
    # Real zipformer decodes of synthesized "Olympia" sentences (2026-09-06).
    assert is_wake_text("OLYMPIAK ET LEUR EST IL")  # glued decode of "Olympia, quelle heure est-il ?"
    assert is_wake_text("SALUE OLYMPIA COMMENÇA VA")
    assert is_wake_text("OLIMPIA")  # bare "Olympia ?" (i spelling)
    assert is_wake_text("EST CE QUE TU M'ENTENDS OLYMPIA")
    assert is_wake_text("SALUOLYMPIA")  # glued neighbour still contains the name
    assert not is_wake_text("ON REGARDE LES JEUX OLYMPIQUES CE SOIR")  # q-continuation
    assert not is_wake_text("LE MONT OLYMPE EST EN GRÈCE")
    assert not is_wake_text("C'EST UN ATHLÈTE OLYMPIEN")
    assert not is_wake_text("LES OLYMPIADES DE MATHS")  # shares the 'a' — explicit exclusion
    assert not is_wake_text("ON VA À BERLIN DEMAIN")
    assert not is_wake_text("IL Y A UN LIT EN PIN")
    assert not is_wake_text("")
    print("ok: wake text matcher")


def test_stop_matcher():
    assert is_stop_text("CHUT OLYMPIA")
    assert is_stop_text("OLYMPIA CHUT")
    assert is_stop_text("OLYMPIA STOP")
    assert is_stop_text("OLIMPIA STOP")  # real decode of "Olympia, stop."
    assert is_stop_text("OLYMPIA CHUTE")  # interjection decoded with trailing e
    assert is_stop_text("OLIMPIA SHU")  # real decode of "Olympia, chut !" (sh onset, t dropped)
    assert is_stop_text("OLYMPIA SUT")  # s onset, t kept
    assert is_stop_text("OLYMPIA CHU")
    assert is_stop_text("CHUS OLYMPIA")
    assert is_stop_text("SU OLYMPIA")  # real decode of "Chut Olympia." — "su" counts only next to the name
    assert is_stop_text("CHUTOLYMPIA")  # glued decode
    assert is_stop_text("OLYMPIASTOP")
    assert not is_stop_text("OLYMPIA QUELLE HEURE EST IL")
    assert not is_stop_text("C'EST QUOI UN PARACHUTE OLYMPIA")  # exact word only
    assert not is_stop_text("OLYMPIA STOPPE LA MUSIQUE")  # no prefix match
    assert not is_stop_text("OLYMPIA J'AI SU LA REPONSE")  # bare "su" away from the name is normal French
    assert not is_stop_text("J'AI SU")
    assert not is_stop_text("")
    print("ok: stop text matcher")


def stream_through(detector, state, pcm16, silence_ms=2000):
    """Feed audio in 20ms chunks, then trailing silence; return fired."""
    before = state._last
    chunk = 320  # 20ms @ 16k
    padded = np.concatenate([
        np.zeros(4800, dtype=np.int16), pcm16, np.zeros(int(16 * silence_ms), dtype=np.int16)
    ])
    for i in range(0, len(padded), chunk):
        detector.feed(padded[i:i + chunk].tobytes(), 16000)
    # wait for the worker to drain the queue
    deadline = time.time() + 15
    while not detector._queue.empty() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    return state._last != before


def test_detector_streaming():
    synth = synth_batch()
    state = WakeState()
    detector = WakeWordDetector(state)
    detector.start()

    positives = [
        "Olympia, quelle heure est-il ?",
        "Salut Olympia, comment ça va ?",
        "Olympia ?",
        "Est-ce que tu m'entends Olympia ?",
    ]
    negatives = [
        "Quelle heure est-il ?",
        "On va à Berlin demain matin.",
        "J'ai pêché un merlan ce matin.",
        "C'est une merveille ce truc.",
    ]
    hits = sum(stream_through(detector, state, synth(t)) for t in positives)
    false = sum(stream_through(detector, state, synth(t)) for t in negatives)
    detector.stop()
    print(f"ok: streaming detector — recall {hits}/{len(positives)}, false wakes {false}/{len(negatives)}")
    assert hits >= 3, f"recall too low: {hits}"
    assert false == 0, f"false wakes: {false}"


def test_detector_stop_streaming():
    synth = synth_batch()
    wake = WakeState()
    stop = StopState()
    detector = WakeWordDetector(wake, stop)
    detector.start()

    stop_positives = [
        "Olympia, chut !",
        "Chut Olympia.",
        "Olympia, stop.",
    ]
    # Normal wake sentences must fire the wake, never the stop.
    hits = 0
    for t in stop_positives:
        # Each phrase must count as a stop on its own decode window — reset
        # the after-wake grace so a previous fire can't carry a lone word.
        # Longer silence tail: a trailing stop word is judged at the decoder
        # endpoint, which can need >2s of silence (live audio never runs out).
        wake._last = 0.0
        hits += stream_through(detector, stop, synth(t), silence_ms=3500)
    false_stop = stream_through(detector, stop, synth("Olympia, quelle heure est-il ?"), silence_ms=3500)
    woke = wake.fired_within(60)
    detector.stop()
    print(f"ok: streaming stop — recall {hits}/{len(stop_positives)}, "
          f"false stop {int(false_stop)}, wake still fires: {woke}")
    assert hits >= 2, f"stop recall too low: {hits}"
    assert not false_stop, "a plain wake sentence fired the stop"
    assert woke, "wake stopped firing with the stop channel wired"
    assert stop.consume(), "no pending interrupt flag after stop fires"
    assert not stop.consume(), "consume must be one-shot"


def test_gatecore_raw_wake():
    """GateCore accepts a mangled transcription when the raw wake fired."""
    import tempfile
    from voice_guard import ENROLL_TARGET, GateCore, HouseholdProfiles, LastBotUtterance

    rng = np.random.default_rng(1)
    fred = rng.standard_normal(512).astype(np.float32)
    fred /= np.linalg.norm(fred)

    def near(seed):
        g = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
        g /= np.linalg.norm(g)
        v = fred + 0.3 * g
        return (v / np.linalg.norm(v)).astype(np.float32)

    with tempfile.TemporaryDirectory() as tmp:
        household = HouseholdProfiles(
            root=Path(tmp) / "voices", pending_path=Path(tmp) / "voices" / ".enrolling"
        )
        household.finish_enrollment()
        profile = household.get_or_create("fred")
        for i in range(ENROLL_TARGET):
            profile.enroll(near(100 + i))

        state = WakeState()
        core = GateCore(household, LastBotUtterance(), wake_state=state,
                        speaker_gate=True, threshold=0.60, require_wake=True)

        # Whisper mangled "Olympia" (into the excluded "Olympe") -> no
        # transcript wake, no raw wake -> drop.
        ok, why = core.evaluate("Olympe, quelle heure est-il", near(1), 2.0)
        assert not ok and "hors attention" in why, why

        # Same mangled text, but the raw-audio engine heard it -> accepted.
        state.fire()
        ok, why = core.evaluate("Olympe, quelle heure est-il", near(2), 2.0)
        assert ok and "éveil par fred" in why, why
        print("ok: GateCore honors raw-audio wake channel")


if __name__ == "__main__":
    test_matcher()
    test_stop_matcher()
    test_gatecore_raw_wake()
    test_detector_streaming()
    test_detector_stop_streaming()
    print("all wake_word tests passed")
