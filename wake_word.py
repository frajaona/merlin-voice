"""Raw-audio wake-word engine for "Olympia".

Runs small streaming ASR models (sherpa-onnx zipformer int8) continuously on
the incoming audio, on their own thread, and fires a WakeState timestamp
whenever an "olympia"-like word shows up in the live decode. The GateCore
treats a recent raw-audio wake as equivalent to seeing the wake word in the
Whisper transcription — so the wake still works when Whisper mangles the
name (with the previous wake word "Merlin", a real session logged "Moulet").

Why a real ASR instead of a dedicated keyword-spotting model: the available
KWS models are English-trained and hear a French name as a different token
sequence every time (measured on "Merlin": MELA/SELEN/MITTLEN/MALLA on four
utterances) — no stable pattern to key on. The French zipformer hears
"Olympia" as OLYMPIA / OLIMPIA.

Engines (ENGINES below) — one per language, selectable per session via
`WakeWordDetector(langs=...)`; several may run on the same audio (each ~70 ms
per utterance, one thread each):
- "fr" (CommonVoice zipformer, 2023-04-14): the historical engine. In
  English mode it STAYS on, because it is the only one that hears "Olympia"
  pronounced the French way (20/20 measured; the English model decodes that
  as "Pierre"). It decodes nothing on English words.
- "en" (LibriSpeech+GigaSpeech zipformer, 2023-06-26): added in English
  mode for the anglicized pronunciation and, above all, for the raw stop
  channel in English ("stop", "hush" — the French engine hears STAPE or
  nothing). Measurements: docs/DECISIONS.md 2026-09-06.

The same decoders carry the stop channel ("Olympia chut/stop", either
order): a StopState fire cuts the in-flight answer and puts the VoiceGate on
privacy hold (see voice_guard.py) without waiting for Whisper.

Wake word history: "Merlin" from the start until 2026-09-06, "Olympia" since
(see docs/DECISIONS.md — the Merlin-era decode measurements live there).
Validated on synthesized speech (tools/test_wake_word.py).
"""
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import CancelFrame, EndFrame, Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_guard import normalize_words

MODELS_DIR = Path(__file__).resolve().parent / "models"
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"

# Shared wake pattern. The streaming decoder glues and misspells. Measured on
# synthesized French (Kokoro ff_siwis, tools/test_wake_word.py, 2026-09-06):
# "Olympia" decodes as OLYMPIA or OLIMPIA (y/i free), "Olympia, quelle heure"
# glued into OLYMPIAK, "Olympia, chut" at fast speed into OLIMPIES.
# Neighbouring real words continue differently after the p: OLYMPIQUES (q),
# OLYMPIEN (e), OLYMPE (e), so requiring an 'a' right after ol[iy]mp[iy]
# keeps them silent. Judged per decoded word (a glued neighbour still
# contains the pattern); the fully joined text would swallow the exclusions.
_WAKE_RE = re.compile(r"ol[iy]mp[iy]a")


@dataclass(frozen=True)
class EngineSpec:
    lang: str
    model_dir: Path
    url: str
    encoder: str
    decoder: str
    joiner: str
    size_hint: str
    # Real words that contain the wake pattern in this language's decodes.
    wake_exclude_re: re.Pattern
    # Whole-word stop words (per decoded word).
    stop_word_re: re.Pattern
    # Stop word glued to the wake word inside one decoded token.
    stop_glued_re: re.Pattern
    # Weak stop variants that count only adjacent to the wake word.
    stop_adjacent_only: frozenset


ENGINES = {
    # Only "olympiade(s)" shares the 'a' in French — excluded per word.
    # Stop: "stop" exact plus the measured decode variants of the
    # interjection "chut" — CHU, CHUS, SUT, CHUTE, SHUT on synthesized
    # speech: the final consonant is unstable and the fricative opens as S
    # or SH. Bare "su" is excluded ("j'ai su…" is normal French) EXCEPT when
    # it sits right next to the wake word in the same decode ("Chut
    # Olympia." measured as SU OLYMPIA — a false stop there costs one
    # re-wake, a missed stop is the privacy failure). "parachute" and
    # "stoppe" stay different words. To be re-tuned on real decodes
    # (MERLIN_WAKE_DEBUG=1) if recall disappoints.
    "fr": EngineSpec(
        lang="fr",
        model_dir=MODELS_DIR / "sherpa-onnx-streaming-zipformer-fr-2023-04-14",
        url=_RELEASE + "sherpa-onnx-streaming-zipformer-fr-2023-04-14.tar.bz2",
        encoder="encoder-epoch-29-avg-9-with-averaged-model.int8.onnx",
        decoder="decoder-epoch-29-avg-9-with-averaged-model.int8.onnx",
        joiner="joiner-epoch-29-avg-9-with-averaged-model.int8.onnx",
        size_hint="~150 MB",
        wake_exclude_re=re.compile(r"ol[iy]mp[iy]ad"),  # olympiade(s)
        stop_word_re=re.compile(r"^stop$|^(chu|shu)(t|te|ts|s)?$|^sut$"),
        stop_glued_re=re.compile(
            r"(chut|shut|stop)e?ol[iy]mp[iy]a|ol[iy]mp[iy]a\w{0,4}(chut|shut|stop)"
        ),
        stop_adjacent_only=frozenset(("su",)),
    ),
    # English decodes (2026-09-06, af_heart/bf_emma/am_michael): OLYMPIA
    # 15/16 (HALYMPIA once at speed 1.15), OLYMPIAN/OLYMPIAD/OLYMPIC/OLYMPUS
    # spelled out — "olympian" CONTAINS the wake pattern, hence the [dn]
    # exclusion. Stop: STOP/HUSH 8/8, "Hush, Olympia" once HUSHED.
    "en": EngineSpec(
        lang="en",
        model_dir=MODELS_DIR / "sherpa-onnx-streaming-zipformer-en-2023-06-26",
        url=_RELEASE + "sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2",
        encoder="encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        decoder="decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        joiner="joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        size_hint="~340 MB download, 80 MB kept",
        wake_exclude_re=re.compile(r"ol[iy]mp[iy]a[dn]"),  # olympiad, olympian
        stop_word_re=re.compile(r"^(stop|hush|hushed|quiet|shut)$"),
        stop_glued_re=re.compile(
            r"(hush|shut|stop|quiet)ol[iy]mp[iy]a|ol[iy]mp[iy]a\w{0,4}(hush|shut|stop|quiet)"
        ),
        stop_adjacent_only=frozenset(),
    ),
}

# Backward-compatible names (French engine).
MODEL_DIR = ENGINES["fr"].model_dir
MODEL_URL = ENGINES["fr"].url


def _is_wake_word_raw(w: str, spec: EngineSpec) -> bool:
    return _WAKE_RE.search(w) is not None and spec.wake_exclude_re.search(w) is None


def _is_stop_word_raw(w: str, spec: EngineSpec) -> bool:
    return spec.stop_word_re.match(w) is not None


# A stop word alone counts if the wake word fired just before — "Olympia…
# chut" often splits across a decoder reset (the wake fire resets the
# stream), and in English mode the French engine fires the wake while the
# English engine hears the stop word.
STOP_AFTER_WAKE_SECS = 3.0


def is_wake_text(text: str, lang: str = "fr") -> bool:
    """True if an "olympia"-like sound appears in the decoded text."""
    spec = ENGINES[lang]
    return any(_is_wake_word_raw(w, spec) for w in normalize_words(text))


def is_stop_text(text: str, lang: str = "fr") -> bool:
    """True if a stop word appears in the decoded text (pairing with the
    wake word is judged by the caller, not here). The glued regex runs
    per-word — gluing happens inside one decoded token; matching the fully
    joined text would false-positive on "parachute olympia"."""
    spec = ENGINES[lang]
    words = normalize_words(text)
    if any(_is_stop_word_raw(w, spec) for w in words):
        return True
    if any(spec.stop_glued_re.search(w) for w in words):
        return True
    # Weak stop variants count only glued to the wake word (see above).
    for i, w in enumerate(words):
        if w in spec.stop_adjacent_only:
            neighbours = words[max(0, i - 1):i] + words[i + 1:i + 2]
            if any(_is_wake_word_raw(n, spec) for n in neighbours):
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


def _ensure_model(spec: EngineSpec):
    if spec.model_dir.exists():
        return
    import tarfile
    import urllib.request

    logger.info(f"downloading {spec.lang} wake-word model ({spec.size_hint}) to {spec.model_dir}")
    spec.model_dir.parent.mkdir(exist_ok=True)
    tmp = spec.model_dir.parent / f"{spec.lang}-model.tar.bz2"
    urllib.request.urlretrieve(spec.url, tmp)
    with tarfile.open(tmp) as tar:
        tar.extractall(spec.model_dir.parent)
    tmp.unlink()
    # Keep only the int8 weights we load (the English tarball ships fp32
    # copies too — 260 MB of dead weight).
    keep = {spec.encoder, spec.decoder, spec.joiner}
    for f in spec.model_dir.glob("*.onnx"):
        if f.name not in keep:
            f.unlink()
    for sub in ("test_wavs",):
        d = spec.model_dir / sub
        if d.is_dir():
            import shutil
            shutil.rmtree(d, ignore_errors=True)


def _load_recognizer(lang: str = "fr"):
    import sherpa_onnx

    spec = ENGINES[lang]
    _ensure_model(spec)
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(spec.model_dir / "tokens.txt"),
        encoder=str(spec.model_dir / spec.encoder),
        decoder=str(spec.model_dir / spec.decoder),
        joiner=str(spec.model_dir / spec.joiner),
        num_threads=2,
        enable_endpoint_detection=True,
    )


class _Engine:
    """One recognizer + its live stream and per-segment wake flag."""

    def __init__(self, lang: str):
        self.spec = ENGINES[lang]
        self.recognizer = _load_recognizer(lang)
        self.stream = self.recognizer.create_stream()
        self.segment_woke = False  # wake already fired for the current decode segment

    def reset(self):
        self.recognizer.reset(self.stream)
        self.segment_woke = False


class WakeWordDetector:
    """Feeds PCM chunks to the streaming recognizer(s) on a worker thread.

    `langs` selects the engines (ENGINES keys) — all of them hear every
    chunk; a wake or stop fires when ANY engine hears it.
    """

    _SENTINEL = object()

    def __init__(self, state: WakeState, stop_state: StopState | None = None,
                 langs: tuple = ("fr",)):
        self._state = state
        self._stop_state = stop_state
        self._langs = tuple(langs) or ("fr",)
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
        should — decode runs ~30x realtime per engine)."""
        try:
            self._queue.put_nowait((pcm, sample_rate))
        except queue.Full:
            pass

    def _run(self):
        engines = []
        for lang in self._langs:
            try:
                engines.append(_Engine(lang))
            except Exception as e:
                logger.error(f"wake-word engine '{lang}' failed to start: {e}")
        if not engines:
            logger.error("no wake-word engine running (transcript wake still works)")
            return
        logger.info(
            "wake-word engine listening (raw audio, zipformer "
            + "+".join(e.spec.lang for e in engines) + ")"
        )
        debug = bool(os.getenv("MERLIN_WAKE_DEBUG"))
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                return
            pcm, sample_rate = item
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            for eng in engines:
                self._step(eng, samples, sample_rate, debug)

    def _step(self, eng: "_Engine", samples: np.ndarray, sample_rate: int, debug: bool):
        rec, stream, lang = eng.recognizer, eng.stream, eng.spec.lang
        stream.accept_waveform(sample_rate, samples)
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        text = rec.get_result(stream)
        endpoint = rec.is_endpoint(stream)
        if debug and text:
            logger.debug(f"wake partial [{lang}]: [{text}] ep={endpoint}")
        words = normalize_words(text)
        # The last word of a live partial may be cut mid-word — a trailing
        # "OLYMPI" could still become "olympique". Judge it only at endpoint.
        candidates = words if endpoint else words[:-1]
        cand_text = " ".join(candidates)
        # Stop outranks wake: "olympia chut" in one segment fires the stop
        # (the earlier in-segment wake fire is harmless — the gate checks
        # the stop first). A lone stop word still counts shortly after a
        # wake fire, for "Olympia… [pause] chut" split across segments — or
        # heard by another engine.
        if self._stop_state is not None and is_stop_text(cand_text, lang) and (
            is_wake_text(cand_text, lang) or self._state.fired_within(STOP_AFTER_WAKE_SECS)
        ):
            logger.info(f"stop phrase heard in raw audio [{lang}]: [{text}]")
            self._stop_state.fire()
            eng.reset()
        elif is_wake_text(cand_text, lang):
            if not eng.segment_woke:
                logger.info(f"wake word heard in raw audio [{lang}]: [{text}]")
                self._state.fire()
                eng.segment_woke = True
            # Keep decoding the segment instead of resetting: an early
            # reset swallows a trailing stop word mid-word (measured with
            # "Merlin": a reset at [MERLIN S] left only [TOP]). The
            # gate reads the wake with 3 s of slack, nothing needs the
            # fire-and-reset. segment_woke prevents refiring meanwhile.
            if endpoint:
                eng.reset()
        elif endpoint:
            eng.reset()


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
