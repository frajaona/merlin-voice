"""Raw-audio wake-word engine for "Olympia".

Runs a small streaming French ASR (sherpa-onnx zipformer int8, CommonVoice)
continuously on the incoming audio, on its own thread, and fires a WakeState
timestamp whenever an "olympia"-like word shows up in the live decode. The
GateCore treats a recent raw-audio wake as equivalent to seeing the wake word
in the Whisper transcription — so the wake still works when Whisper mangles
the name (with the previous wake word "Merlin", a real session logged "Moulet").

Why a French ASR instead of a dedicated keyword-spotting model: the available
KWS models are English-trained and hear a French name as a different token
sequence every time (measured on "Merlin": MELA/SELEN/MITTLEN/MALLA on four
utterances) — no stable pattern to key on. The French zipformer hears
"Olympia" as OLYMPIA / OLIMPIA.

The same decoder carries the stop channel ("Olympia chut/stop", either order):
a StopState fire cuts the in-flight answer and puts the VoiceGate on privacy
hold (see voice_guard.py) without waiting for Whisper.

Wake word history: "Merlin" from the start until 2026-09-06, "Olympia" since
(see docs/DECISIONS.md — the Merlin-era decode measurements live there).
Validated on synthesized French speech (tools/test_wake_word.py).
"""
import os
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import CancelFrame, EndFrame, Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_guard import normalize_words

MODEL_DIR = Path(__file__).resolve().parent / "models" / "sherpa-onnx-streaming-zipformer-fr-2023-04-14"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-streaming-zipformer-fr-2023-04-14.tar.bz2"
)


# The streaming decoder glues and misspells. Measured on synthesized French
# (Kokoro ff_siwis, tools/test_wake_word.py, 2026-09-06): "Olympia" decodes
# as OLYMPIA or OLIMPIA (y/i free), "Olympia, quelle heure" glued into
# OLYMPIAK, "Olympia, chut" at fast speed into OLIMPIES. Neighbouring real
# words continue differently after the p: OLYMPIQUES (q), OLYMPIEN (e),
# OLYMPE (e), so requiring an 'a' right after ol[iy]mp[iy] keeps them
# silent. Only "olympiade(s)" shares the 'a' — excluded per word below.
# Judged per decoded word (a glued neighbour still contains the pattern);
# the fully joined text would swallow the olympiade exclusion.
_WAKE_RE = re.compile(r"ol[iy]mp[iy]a")
_WAKE_EXCLUDE_RE = re.compile(r"ol[iy]mp[iy]ad")  # olympiade(s)

# Stop channel: "stop" exact plus the measured decode variants of the
# interjection "chut" — CHU, CHUS, SUT, CHUTE, SHUT on synthesized speech:
# the final consonant is unstable and the fricative opens as S or SH. Bare
# "su" is excluded ("j'ai su…" is normal French) EXCEPT when it sits right
# next to the wake word in the same decode ("Chut Olympia." measured as
# SU OLYMPIA — a false stop there costs one re-wake, a missed stop is the
# privacy failure). "parachute" and "stoppe" stay different words.
# Glued-adjacency forms cover the decoder gluing neighbours. To be re-tuned
# on real decodes (MERLIN_WAKE_DEBUG=1) if recall disappoints.
_STOP_CHUT_RE = re.compile(r"^(chu|shu)(t|te|ts|s)?$|^sut$")
_STOP_GLUED_RE = re.compile(r"(chut|shut|stop)e?ol[iy]mp[iy]a|ol[iy]mp[iy]a\w{0,4}(chut|shut|stop)")
_STOP_ADJACENT_ONLY = frozenset(("su",))


def _is_wake_word_raw(w: str) -> bool:
    return _WAKE_RE.search(w) is not None and _WAKE_EXCLUDE_RE.search(w) is None


def _is_stop_word_raw(w: str) -> bool:
    return w == "stop" or _STOP_CHUT_RE.match(w) is not None

# A stop word alone counts if the wake word fired just before — "Olympia…
# chut" often splits across a decoder reset (the wake fire resets the stream).
STOP_AFTER_WAKE_SECS = 3.0


def is_wake_text(text: str) -> bool:
    """True if an "olympia"-like sound appears in the decoded text."""
    return any(_is_wake_word_raw(w) for w in normalize_words(text))


def is_stop_text(text: str) -> bool:
    """True if a stop word appears in the decoded text (pairing with the
    wake word is judged by the caller, not here). The glued regex runs
    per-word — gluing happens inside one decoded token; matching the fully
    joined text would false-positive on "parachute olympia"."""
    words = normalize_words(text)
    if any(_is_stop_word_raw(w) for w in words):
        return True
    if any(_STOP_GLUED_RE.search(w) for w in words):
        return True
    # Weak stop variants count only glued to the wake word (see above).
    for i, w in enumerate(words):
        if w in _STOP_ADJACENT_ONLY:
            neighbours = words[max(0, i - 1):i] + words[i + 1:i + 2]
            if any(_is_wake_word_raw(n) for n in neighbours):
                return True
    return False


class WakeState:
    """Thread-safe 'when did the wake word last fire' flag."""

    def __init__(self):
        self._last = 0.0

    def fire(self):
        self._last = time.monotonic()

    def fired_within(self, secs: float) -> bool:
        return time.monotonic() - self._last <= secs

    @property
    def last(self) -> float:
        return self._last


class StopState(WakeState):
    """WakeState plus a one-shot flag the pipeline consumes to cut TTS."""

    def __init__(self):
        super().__init__()
        self._pending = False

    def fire(self):
        super().fire()
        self._pending = True

    def consume(self) -> bool:
        if self._pending:
            self._pending = False
            return True
        return False


def _load_recognizer():
    import sherpa_onnx

    if not MODEL_DIR.exists():
        import tarfile
        import urllib.request

        logger.info(f"downloading French wake-word model (~150 MB) to {MODEL_DIR}")
        MODEL_DIR.parent.mkdir(exist_ok=True)
        tmp = MODEL_DIR.parent / "fr-model.tar.bz2"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        with tarfile.open(tmp) as tar:
            tar.extractall(MODEL_DIR.parent)
        tmp.unlink()

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(MODEL_DIR / "tokens.txt"),
        encoder=str(MODEL_DIR / "encoder-epoch-29-avg-9-with-averaged-model.int8.onnx"),
        decoder=str(MODEL_DIR / "decoder-epoch-29-avg-9-with-averaged-model.int8.onnx"),
        joiner=str(MODEL_DIR / "joiner-epoch-29-avg-9-with-averaged-model.int8.onnx"),
        num_threads=2,
        enable_endpoint_detection=True,
    )


class WakeWordDetector:
    """Feeds PCM chunks to the streaming recognizer on a worker thread."""

    _SENTINEL = object()

    def __init__(self, state: WakeState, stop_state: StopState | None = None):
        self._state = state
        self._stop_state = stop_state
        self._queue: queue.Queue = queue.Queue(maxsize=400)
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True, name="wake-word")
            self._thread.start()

    def stop(self):
        try:
            self._queue.put_nowait(self._SENTINEL)
        except queue.Full:
            pass

    def feed(self, pcm: bytes, sample_rate: int):
        """Non-blocking; drops chunks if the worker falls behind (it never
        should — decode runs ~30x realtime)."""
        try:
            self._queue.put_nowait((pcm, sample_rate))
        except queue.Full:
            pass

    def _run(self):
        try:
            recognizer = _load_recognizer()
        except Exception as e:
            logger.error(f"wake-word engine failed to start (transcript wake still works): {e}")
            return
        logger.info("wake-word engine listening (French zipformer, raw audio)")
        stream = recognizer.create_stream()
        segment_woke = False  # wake already fired for the current decode segment
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                return
            pcm, sample_rate = item
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            stream.accept_waveform(sample_rate, samples)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            text = recognizer.get_result(stream)
            endpoint = recognizer.is_endpoint(stream)
            if os.getenv("MERLIN_WAKE_DEBUG") and text:
                logger.debug(f"wake partial: [{text}] ep={endpoint}")
            words = normalize_words(text)
            # The last word of a live partial may be cut mid-word — a trailing
            # "OLYMPI" could still become "olympique". Judge it only at endpoint.
            candidates = words if endpoint else words[:-1]
            cand_text = " ".join(candidates)
            # Stop outranks wake: "olympia chut" in one segment fires the stop
            # (the earlier in-segment wake fire is harmless — the gate checks
            # the stop first). A lone stop word still counts shortly after a
            # wake fire, for "Olympia… [pause] chut" split across segments.
            if self._stop_state is not None and is_stop_text(cand_text) and (
                is_wake_text(cand_text) or self._state.fired_within(STOP_AFTER_WAKE_SECS)
            ):
                logger.info(f"stop phrase heard in raw audio: [{text}]")
                self._stop_state.fire()
                recognizer.reset(stream)
                segment_woke = False
            elif is_wake_text(cand_text):
                if not segment_woke:
                    logger.info(f"wake word heard in raw audio: [{text}]")
                    self._state.fire()
                    segment_woke = True
                # Keep decoding the segment instead of resetting: an early
                # reset swallows a trailing stop word mid-word (measured with
                # "Merlin": a reset at [MERLIN S] left only [TOP]). The
                # gate reads the wake with 3 s of slack, nothing needs the
                # fire-and-reset. segment_woke prevents refiring meanwhile.
                if endpoint:
                    recognizer.reset(stream)
                    segment_woke = False
            elif endpoint:
                recognizer.reset(stream)
                segment_woke = False


class WakeWordListener(FrameProcessor):
    """Taps the raw input audio and feeds it to the WakeWordDetector.

    Placement: right after transport.input(), before the VAD — it must hear
    everything, not only VAD-approved segments.

    When a stop_state is wired, a raw-audio stop fire is acted on here within
    one audio frame (~20 ms): the in-flight answer is interrupted and on_stop
    (GateCore.enter_hold) is called — no waiting for VAD stop + Whisper.
    """

    def __init__(self, detector: WakeWordDetector, *, stop_state: StopState | None = None,
                 on_stop=None):
        super().__init__()
        self._detector = detector
        self._stop_state = stop_state
        self._on_stop = on_stop

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._detector.start()
            self._detector.feed(frame.audio, frame.sample_rate)
            if self._stop_state is not None and self._stop_state.consume():
                await self.broadcast_interruption()
                if self._on_stop is not None:
                    self._on_stop()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._detector.stop()
        await self.push_frame(frame, direction)
