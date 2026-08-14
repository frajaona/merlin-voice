"""Raw-audio wake-word engine for "Merlin".

Runs a small streaming French ASR (sherpa-onnx zipformer int8, CommonVoice)
continuously on the incoming audio, on its own thread, and fires a WakeState
timestamp whenever a "merlin"-like word shows up in the live decode. The
GateCore treats a recent raw-audio wake as equivalent to seeing the wake word
in the Whisper transcription — so the wake still works when Whisper mangles
"Merlin" into something else (a real session logged it as "Moulet").

Why a French ASR instead of a dedicated keyword-spotting model: the available
KWS models are English-trained and hear French "Merlin" as a different token
sequence every time (measured: MELA/SELEN/MITTLEN/MALLA on four utterances) —
no stable pattern to key on. The French zipformer hears it as MERLIN.

Validated on synthesized French speech (tools/test_wake_word.py):
10/11 correct including "Berlin"/"merlan"/"merveille" rejections.
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


# The streaming decoder glues and misspells: real "Salut Merlin" was decoded
# as SALUMEERLIN, "Merlin ?" as MERLINGUE, and the first vowel can come out
# as 'a'. Match the character stream (spaces removed) for m + e/a vowel(s) +
# rl + an i/y continuation — merlan/merlot/merle continue with a/o/e after
# the l and stay silent. Bare "Merlin ?" occasionally decodes without the i
# (SMERLAND) and is then caught by the Whisper transcript channel instead.
_WAKE_RE = re.compile(r"m[ae]{1,2}rl[iy]")


def is_wake_text(text: str) -> bool:
    """True if a "merlin"-like sound appears in the decoded text."""
    return _WAKE_RE.search("".join(normalize_words(text))) is not None


class WakeState:
    """Thread-safe 'when did the wake word last fire' flag."""

    def __init__(self):
        self._last = 0.0

    def fire(self):
        self._last = time.monotonic()

    def fired_within(self, secs: float) -> bool:
        return time.monotonic() - self._last <= secs


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

    def __init__(self, state: WakeState):
        self._state = state
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
            # "MERL" could still become "merlan". Judge it only at endpoint.
            candidates = words if endpoint else words[:-1]
            if is_wake_text(" ".join(candidates)):
                logger.info(f"wake word heard in raw audio: [{text}]")
                self._state.fire()
                recognizer.reset(stream)  # don't re-fire on the same decode
            elif endpoint:
                recognizer.reset(stream)


class WakeWordListener(FrameProcessor):
    """Taps the raw input audio and feeds it to the WakeWordDetector.

    Placement: right after transport.input(), before the VAD — it must hear
    everything, not only VAD-approved segments.
    """

    def __init__(self, detector: WakeWordDetector):
        super().__init__()
        self._detector = detector

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._detector.start()
            self._detector.feed(frame.audio, frame.sample_rate)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._detector.stop()
        await self.push_frame(frame, direction)
