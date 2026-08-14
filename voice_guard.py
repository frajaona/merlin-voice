"""Voice guard — keeps Merlin usable in public and in a full household.

Three defenses between the microphone and the LLM:

1. Hardened STT (GuardedWhisperSTT): better Whisper decoding (French initial
   prompt, no cross-segment conditioning) plus per-segment filtering of the
   classic French hallucinations ("Sous-titrage ST' 501", "Merci d'avoir
   regardé…", repeated-phrase loops, punctuation-only output).

2. Household speaker gate: one voice profile per enrolled person in
   data/voices/<name>.npz. Only enrolled voices can talk to Merlin.
   Enrollment: `tools/voice_profile.py enroll <name>`, then that person chats
   with Merlin alone until their profile completes (8 utterances).

3. Attention gate with activator binding: an exchange opens with the wake
   word ("Merlin") spoken by an enrolled voice — that person becomes the
   *activator* and only their voice is answered for the rest of the exchange,
   even if other household members chime in. Saying "Merlin…" again passes
   the mic. Attention stays open while the bot speaks and for a follow-up
   window after it stops (longer when the bot asked a question); outside
   attention everything is ignored.

Rejected utterances are still written to the transcript store with a
"[filtré: …]" prefix so the STT test set keeps its misrecognition examples
and thresholds can be tuned from real data.

Environment knobs (all optional):
    MERLIN_STT_MODEL          HF repo of the MLX Whisper model
    MERLIN_STT_PROMPT_EXTRA   extra vocabulary appended to the initial prompt
    MERLIN_SPEAKER_GATE       "off" to disable speaker verification
    MERLIN_SPEAKER_THRESHOLD  cosine similarity acceptance threshold (0.60)
    MERLIN_FAMILY_MODE        "1": any enrolled voice accepted mid-exchange
                              (no activator binding — friendlier at home,
                              weaker in public)
    MERLIN_REQUIRE_WAKE       "0" to disable the attention gate
    MERLIN_FOLLOWUP_SECS      follow-up window after the bot stops (12)
    MERLIN_QUESTION_SECS      follow-up window after a bot question (30)
"""
import asyncio
import io
import os
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.whisper.stt import WhisperSTTServiceMLX
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

DATA_DIR = Path(__file__).resolve().parent / "data"
LEGACY_PROFILE_PATH = DATA_DIR / "voice_profile.npz"
VOICES_DIR = DATA_DIR / "voices"
PENDING_PATH = VOICES_DIR / ".enrolling"
VOCAB_PATH = DATA_DIR / "stt_vocab.txt"
SPEAKER_MODEL_PATH = Path(__file__).resolve().parent / "models" / "speaker_campplus_voxceleb.onnx"

WAKE_PREFIX = "merl"  # matches "merlin" and close mishearings
# Real French words that start like the wake word — never wake on these.
WAKE_EXCLUDE = {"merlan", "merlans", "merlant", "merle", "merles", "merlu", "merlus", "merlot", "merlots"}


def is_wake_word(word: str) -> bool:
    return word.startswith(WAKE_PREFIX) and word not in WAKE_EXCLUDE

STT_MODEL = os.getenv("MERLIN_STT_MODEL", "mlx-community/whisper-large-v3-turbo")
SPEAKER_GATE_ENABLED = os.getenv("MERLIN_SPEAKER_GATE", "on").lower() not in ("off", "0", "false")
# Calibrated 2026-08-13 on real data: owner's utterances score 0.72-0.89
# against his profile, another speaker on the same phone scored 0.08-0.54.
SPEAKER_THRESHOLD = float(os.getenv("MERLIN_SPEAKER_THRESHOLD", "0.60"))
FAMILY_MODE = os.getenv("MERLIN_FAMILY_MODE", "0").lower() in ("1", "on", "true")
REQUIRE_WAKE = os.getenv("MERLIN_REQUIRE_WAKE", "1").lower() not in ("off", "0", "false")
FOLLOWUP_SECS = float(os.getenv("MERLIN_FOLLOWUP_SECS", "12"))
QUESTION_SECS = float(os.getenv("MERLIN_QUESTION_SECS", "30"))

# Enrollment
ENROLL_TARGET = 8          # profile is complete after this many utterances
ENROLL_MIN_SECS = 1.2      # only enroll utterances at least this long
ENROLL_MIN_WORDS = 3
ADAPT_SIM = 0.75           # keep refining a profile on unmistakable matches
                           # (0.55 once let a same-room bystander in)
PROFILE_MAX = 24           # rolling cap on stored embeddings per person
VERIFY_MIN_SECS = 1.0      # embeddings of shorter clips are too unstable to
                           # judge (a real "Merlin ?" scored 0.22 vs its owner)
VERIFY_MIN_WORDS = 3       # duration alone overstates content: it includes
                           # ~1s of VAD buffer, so a one-word "Non." measures
                           # >1s yet embedded at sim 0.08 vs its own speaker
SHORT_WAKE_SIM = 0.35      # lenient bar for identifying a short wake utterance
ANCHOR_MAX = 10            # embeddings kept from the activator's exchange

# Whisper hallucination filters
NO_SPEECH_MAX = 0.55       # drop segments Whisper itself doubts contain speech
LOGPROB_MIN = -1.1         # drop very low-confidence segments
_BLOCK_MARKERS = (
    "sous titrage",
    "sous titres",
    "amara org",
    "abonnez vous",
    "merci d avoir regarde",
    "n oubliez pas de vous abonner",
)


def normalize_words(text: str) -> list:
    """Lowercase, strip accents and punctuation, split into words."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return [w for w in "".join(c if c.isalnum() else " " for c in text).split() if w]


def looks_hallucinated(text: str) -> str | None:
    """Return a reason string if the transcription matches a known Whisper
    hallucination pattern, else None."""
    words = normalize_words(text)
    if not words:
        return "vide"
    joined = " ".join(words)
    for marker in _BLOCK_MARKERS:
        if marker in joined:
            return f"motif connu ({marker})"
    # Repetition loops: "t'es pas qu'on peut" ×4 etc.
    if len(words) >= 8 and len(set(words)) / len(words) < 0.4:
        return "boucle de repetition"
    return None


class LastBotUtterance:
    """Most recent sentence the bot synthesized (to detect questions)."""

    def __init__(self):
        self.text = ""

    @property
    def is_question(self) -> bool:
        return self.text.rstrip().endswith("?")


# ---------------------------------------------------------------------------
# Speaker embeddings
# ---------------------------------------------------------------------------

_extractor = None

SPEAKER_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/wespeaker_en_voxceleb_CAM%2B%2B.onnx"
)


def _get_extractor():
    """Lazy module-level singleton — shared across sessions."""
    global _extractor
    if _extractor is None:
        import sherpa_onnx

        if not SPEAKER_MODEL_PATH.exists():
            import urllib.request

            logger.info(f"downloading speaker model (~29 MB) to {SPEAKER_MODEL_PATH}")
            SPEAKER_MODEL_PATH.parent.mkdir(exist_ok=True)
            urllib.request.urlretrieve(SPEAKER_MODEL_URL, SPEAKER_MODEL_PATH)

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(SPEAKER_MODEL_PATH), num_threads=2
        )
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        logger.info(f"speaker embedding model loaded (dim={_extractor.dim})")
    return _extractor


def compute_embedding(audio_f32: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Speaker embedding of one utterance, L2-normalized. Blocking (~10ms)."""
    extractor = _get_extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate, audio_f32)
    stream.input_finished()
    embedding = np.asarray(extractor.compute(stream), dtype=np.float32)
    norm = np.linalg.norm(embedding)
    return embedding / norm if norm > 0 else embedding


def _normed_mean(embeddings: list) -> np.ndarray:
    mean = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else mean


# ---------------------------------------------------------------------------
# Household profiles
# ---------------------------------------------------------------------------

class PersonProfile:
    """Rolling set of one person's voice embeddings, persisted to disk."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self._path = path
        self._embeddings: list = []
        if path.exists():
            try:
                data = np.load(path)
                self._embeddings = [e for e in data["embeddings"]]
            except Exception as e:
                logger.warning(f"voice profile '{name}' unreadable, starting fresh: {e}")

    @property
    def count(self) -> int:
        return len(self._embeddings)

    @property
    def complete(self) -> bool:
        return self.count >= ENROLL_TARGET

    def similarity(self, embedding: np.ndarray) -> float:
        return float(np.dot(_normed_mean(self._embeddings), embedding))

    def enroll(self, embedding: np.ndarray):
        self._embeddings.append(embedding)
        if len(self._embeddings) > PROFILE_MAX:
            self._embeddings.pop(0)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self._path, embeddings=np.stack(self._embeddings))


class HouseholdProfiles:
    """All enrolled voices (data/voices/*.npz) plus the pending enrollment.

    Enrollment is opened with `tools/voice_profile.py enroll <name>` (writes
    the name to voices/.enrolling). While pending, utterances that don't match
    an existing member are enrolled into that person's profile; the marker is
    cleared when the profile completes.
    """

    def __init__(self, root: Path = VOICES_DIR, pending_path: Path = PENDING_PATH):
        self._root = root
        self._pending_path = pending_path
        root.mkdir(parents=True, exist_ok=True)

        # Migrate the single-owner profile from before household support.
        # Only for the real voices dir — never when a test passes a temp root.
        if root == VOICES_DIR and LEGACY_PROFILE_PATH.exists() and not any(root.glob("*.npz")):
            LEGACY_PROFILE_PATH.rename(root / "fred.npz")
            logger.info("migrated legacy voice profile to voices/fred.npz")

        self.people = {p.stem: PersonProfile(p.stem, p) for p in sorted(root.glob("*.npz"))}

        # Fresh install: first person to talk enrolls as the owner.
        if not self.people and self.pending_name() is None:
            self.start_enrollment("proprietaire")

        summary = ", ".join(
            f"{p.name} ({p.count}/{ENROLL_TARGET})" for p in self.people.values()
        ) or "aucun"
        pending = self.pending_name()
        logger.info(
            f"voice profiles: {summary}"
            + (f" — enrollment open for '{pending}'" if pending else "")
        )

    def pending_name(self) -> str | None:
        # Re-read each time: `tools/voice_profile.py enroll` can run mid-session.
        if self._pending_path.exists():
            name = self._pending_path.read_text(encoding="utf-8").split()
            return name[0] if name else None
        return None

    def pending_target(self) -> int:
        # Optional second token: enrollment target ("fred 17" = top-up to 17).
        if self._pending_path.exists():
            parts = self._pending_path.read_text(encoding="utf-8").split()
            if len(parts) > 1 and parts[1].isdigit():
                return int(parts[1])
        return ENROLL_TARGET

    def start_enrollment(self, name: str, target: int | None = None):
        self._pending_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending_path.write_text(
            f"{name} {target}" if target else name, encoding="utf-8"
        )
        logger.info(f"enrollment open for '{name}'" + (f" (target {target})" if target else ""))

    def finish_enrollment(self):
        self._pending_path.unlink(missing_ok=True)

    def get_or_create(self, name: str) -> PersonProfile:
        if name not in self.people:
            self.people[name] = PersonProfile(name, self._root / f"{name}.npz")
        return self.people[name]

    def best_match(self, embedding) -> tuple:
        """(name, similarity) of the closest enrolled profile, or (None, None)."""
        if embedding is None:
            return None, None
        candidates = [p for p in self.people.values() if p.count > 0]
        if not candidates:
            return None, None
        best = max(candidates, key=lambda p: p.similarity(embedding))
        return best.name, best.similarity(embedding)


# ---------------------------------------------------------------------------
# Gate logic (pure — no pipecat dependency, directly unit-testable)
# ---------------------------------------------------------------------------

class GateCore:
    """Decides which utterances Merlin answers.

    Rules (speaker gate on, wake required — the defaults):
    - Outside attention, only the wake word opens an exchange, and only if the
      voice matches an enrolled profile. That person becomes the activator.
    - Inside an exchange, only the activator's voice is accepted (compared to
      both their profile and the live anchor built from this exchange's
      utterances, which shares mic/room conditions). The wake word re-binds
      the exchange to whoever says it. MERLIN_FAMILY_MODE=1 accepts any
      enrolled member mid-exchange instead.
    - Utterances < VERIFY_MIN_SECS can't be reliably verified: they pass
      inside an exchange, and at activation they get a lenient identity bar.
    - Embedding extraction failure fails open — never lock the household out.
    """

    def __init__(
        self,
        household: HouseholdProfiles,
        last_bot: LastBotUtterance,
        *,
        speaker_gate: bool = SPEAKER_GATE_ENABLED,
        threshold: float = SPEAKER_THRESHOLD,
        family_mode: bool = FAMILY_MODE,
        require_wake: bool = REQUIRE_WAKE,
        followup_secs: float = FOLLOWUP_SECS,
        question_secs: float = QUESTION_SECS,
        wake_state=None,  # wake_word.WakeState — raw-audio wake channel
        now=time.monotonic,
    ):
        self.household = household
        self._last_bot = last_bot
        self._wake_state = wake_state
        self._speaker_gate = speaker_gate
        self._threshold = threshold
        self._family_mode = family_mode
        self._require_wake = require_wake
        self._followup_secs = followup_secs
        self._question_secs = question_secs
        self._now = now
        self._bot_speaking = False
        self._attentive_until = 0.0
        self.activator: str | None = None
        self._anchor: list = []

    # -- attention bookkeeping ------------------------------------------------

    def on_bot_started(self):
        self._bot_speaking = True

    def on_bot_stopped(self):
        self._bot_speaking = False
        window = self._question_secs if self._last_bot.is_question else self._followup_secs
        self._attentive_until = self._now() + window

    def _attentive(self) -> bool:
        return self._bot_speaking or self._now() < self._attentive_until

    def _touch_attention(self):
        self._attentive_until = max(self._attentive_until, self._now() + self._followup_secs)

    # -- helpers ----------------------------------------------------------------

    def _anchor_sim(self, embedding) -> float | None:
        if not self._anchor or embedding is None:
            return None
        return float(np.dot(_normed_mean(self._anchor), embedding))

    def _bind(self, name: str | None, embedding=None):
        self.activator = name
        self._anchor = [embedding] if embedding is not None else []

    def _extend_anchor(self, embedding):
        self._anchor.append(embedding)
        if len(self._anchor) > ANCHOR_MAX:
            self._anchor.pop(0)

    def _adapt(self, name: str | None, sim, embedding):
        if name and sim is not None and sim >= ADAPT_SIM:
            self.household.people[name].enroll(embedding)

    # -- decision -----------------------------------------------------------------

    def evaluate(self, text: str, embedding, duration: float) -> tuple:
        """(accept, reason) for one transcribed utterance."""
        words = normalize_words(text)
        wake = any(is_wake_word(w) for w in words)
        # Raw-audio channel: the wake-word engine may have caught "Merlin"
        # even when Whisper mangled it. The fire must fall inside this
        # utterance's window (its duration plus a little slack).
        if not wake and self._wake_state is not None:
            wake = self._wake_state.fired_within(duration + 3.0)

        attentive = not self._require_wake or self._attentive()
        if not attentive:
            self._bind(None)  # previous exchange is over
            if not wake:
                return False, "hors attention (pas de mot d'éveil)"

        if not self._speaker_gate:
            self._touch_attention()
            return True, "gate locuteur désactivé"

        if embedding is None:  # extraction failed — fail open, keep binding as-is
            self._touch_attention()
            return True, "vérification indisponible"

        verified = duration >= VERIFY_MIN_SECS and len(words) >= VERIFY_MIN_WORDS
        name, sim = self.household.best_match(embedding)
        known = name is not None and sim is not None and sim >= self._threshold

        # Enrollment session: unmatched voices are the enrollee — and so is a
        # voice whose best match is the half-built pending profile itself.
        pending = self.household.pending_name()
        if pending and (name == pending or not (known and verified)):
            return self._handle_enrollment(pending, words, embedding, duration)

        # Mid-exchange: is this the activator continuing?
        if self.activator is not None and attentive:
            if not verified:
                self._touch_attention()
                return True, f"{self.activator} (court, non vérifié)"
            anchor_sim = self._anchor_sim(embedding)
            is_activator = (known and name == self.activator) or (
                anchor_sim is not None and anchor_sim >= self._threshold
            )
            if is_activator or (self._family_mode and known):
                who = name if (self._family_mode and known) else self.activator
                if is_activator:
                    self._extend_anchor(embedding)
                    # Adapt on unmistakable matches — vs the stored profile,
                    # or vs this exchange's live anchor (same mic and room,
                    # and the activator already passed the wake bar). This is
                    # how the profile learns far-from-phone and soft speech.
                    strong_anchor = anchor_sim is not None and anchor_sim >= 0.80
                    if name == self.activator and sim is not None and (
                        sim >= ADAPT_SIM or (strong_anchor and sim >= 0.45)
                    ):
                        self.household.people[name].enroll(embedding)
                elif known:
                    self._adapt(name, sim, embedding)
                self._touch_attention()
                extra = f", ancre={anchor_sim:.2f}" if anchor_sim is not None else ""
                return True, f"{who} (sim={sim:.2f}{extra})"
            if wake and known:  # someone else takes the mic
                self._bind(name, embedding)
                self._adapt(name, sim, embedding)
                self._touch_attention()
                return True, f"éveil, nouvel activateur {name} (sim={sim:.2f})"
            return False, (
                f"pas l'activateur ({self.activator}) — meilleur profil "
                f"{name or 'aucun'} (sim={0.0 if sim is None else sim:.2f})"
            )

        # Activation: opening (or re-opening) an exchange.
        if verified:
            if known:
                self._bind(name, embedding)
                self._adapt(name, sim, embedding)
                self._touch_attention()
                return True, f"éveil par {name} (sim={sim:.2f})"
            return False, f"voix inconnue (sim={0.0 if sim is None else sim:.2f})"
        # Short wake ("Merlin ?"): embeddings too unstable for the full bar.
        if name is not None and sim is not None and sim >= SHORT_WAKE_SIM:
            self._bind(name)  # anchor starts on the first verified utterance
            self._touch_attention()
            return True, f"éveil par {name} (court, sim={sim:.2f})"
        return False, f"voix inconnue (court, sim={0.0 if sim is None else sim:.2f})"

    def _handle_enrollment(self, pending: str, words, embedding, duration) -> tuple:
        profile = self.household.get_or_create(pending)
        target = self.household.pending_target()
        note = ""
        if duration >= ENROLL_MIN_SECS and len(words) >= ENROLL_MIN_WORDS:
            # Don't absorb a clearly different voice into a half-built profile.
            suspicious = profile.count >= 2 and profile.similarity(embedding) < 0.30
            if not suspicious:
                profile.enroll(embedding)
                note = f", {profile.count}/{target}"
                if profile.count >= target:
                    self.household.finish_enrollment()
                    logger.info(f"enrollment of '{pending}' complete — voice ACTIVE")
        self._bind(pending, embedding if duration >= VERIFY_MIN_SECS else None)
        self._touch_attention()
        return True, f"inscription {pending}{note}"


# ---------------------------------------------------------------------------
# Pipecat wrappers
# ---------------------------------------------------------------------------

def _initial_prompt() -> str:
    prompt = (
        "Discussion en français avec Merlin, un assistant vocal. "
        "Météo, minuteur, actualités, l'Île d'Yeu, La Rochelle, Bordeaux."
    )
    if VOCAB_PATH.exists():
        vocab = [l.strip() for l in VOCAB_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        if vocab:
            prompt += " " + ", ".join(vocab) + "."
    extra = os.getenv("MERLIN_STT_PROMPT_EXTRA", "").strip()
    if extra:
        prompt += " " + extra
    return prompt


class GuardedWhisperSTT(WhisperSTTServiceMLX):
    """MLX Whisper with better decoding and hallucination filtering.

    Also computes the utterance's speaker embedding and attaches it (plus the
    speech duration) to the TranscriptionFrame for the VoiceGate downstream.
    """

    def __init__(self, *, compute_speaker_embedding: bool = True, log_fn=None, **kwargs):
        super().__init__(**kwargs)
        self._compute_speaker_embedding = compute_speaker_embedding
        self._log_fn = log_fn  # log_fn(text) — records filtered utterances
        self._prompt = _initial_prompt()

    def _pcm_to_float(self, audio: bytes) -> np.ndarray:
        # SegmentedSTTService hands us a WAV container — skip the header
        # instead of decoding it as samples.
        if audio[:4] == b"RIFF":
            with wave.open(io.BytesIO(audio)) as w:
                audio = w.readframes(w.getnframes())
        audio_f32 = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        rate = self.sample_rate or 16000
        if rate != 16000:  # Whisper and CAM++ both want 16 kHz
            target_len = int(len(audio_f32) * 16000 / rate)
            audio_f32 = np.interp(
                np.linspace(0, len(audio_f32) - 1, target_len),
                np.arange(len(audio_f32)),
                audio_f32,
            ).astype(np.float32)
        return audio_f32

    async def run_stt(self, audio: bytes):
        try:
            import mlx_whisper

            await self.start_processing_metrics()
            audio_f32 = self._pcm_to_float(audio)
            duration = len(audio_f32) / 16000.0

            # Near-silent segments (breath, rustle that slipped past VAD) make
            # Whisper hallucinate confident interjections ("Merci.") — skip
            # them before spending a transcription on it.
            rms = float(np.sqrt(np.mean(audio_f32**2))) if len(audio_f32) else 0.0
            if rms < 0.0035:
                logger.debug(f"STT: skipping near-silent segment (rms={rms:.4f}, {duration:.1f}s)")
                await self.stop_processing_metrics()
                return

            result = await asyncio.to_thread(
                mlx_whisper.transcribe,
                audio_f32,
                path_or_hf_repo=self._settings.model,
                language="fr",
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=self._prompt,
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
            )

            parts = []
            for segment in result.get("segments", []):
                if segment.get("compression_ratio") == 0.5555555555555556:
                    continue  # known hallucination fingerprint
                if segment.get("no_speech_prob", 0.0) > NO_SPEECH_MAX:
                    continue
                if segment.get("avg_logprob", 0.0) < LOGPROB_MIN:
                    logger.info(f"STT: dropping low-confidence segment [{segment.get('text', '').strip()}]")
                    continue
                parts.append(segment.get("text", ""))
            text = " ".join(p.strip() for p in parts if p.strip()).strip()

            await self.stop_processing_metrics()

            if not text:
                return
            reason = looks_hallucinated(text)
            if reason:
                logger.info(f"STT: dropping hallucination ({reason}): [{text}]")
                if self._log_fn:
                    self._log_fn(f"[filtré: hallucination {reason}] {text}")
                return

            frame = TranscriptionFrame(text, self._user_id, time_now_iso8601(), Language.FR)
            frame.speech_secs = duration
            frame.speaker_embedding = None
            if self._compute_speaker_embedding:
                try:
                    frame.speaker_embedding = await asyncio.to_thread(compute_embedding, audio_f32)
                except Exception as e:
                    logger.warning(f"speaker embedding failed (gate will fail open): {e}")
            logger.debug(f"Transcription ({duration:.1f}s): [{text}]")
            yield frame

        except Exception as e:
            yield ErrorFrame(error=f"STT error: {e}")


class VoiceGate(FrameProcessor):
    """Pipecat processor wrapping GateCore.

    Placement: directly after the STT service, before the transcript logger
    and the user context aggregator — a dropped frame never starts a turn.
    """

    def __init__(self, *, core: GateCore, log_fn=None):
        super().__init__()
        self._core = core
        self._log_fn = log_fn

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._core.on_bot_started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._core.on_bot_stopped()
        elif isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            accept, reason = self._core.evaluate(
                frame.text,
                getattr(frame, "speaker_embedding", None),
                getattr(frame, "speech_secs", 0.0),
            )
            if not accept:
                logger.info(f"VoiceGate: dropped [{frame.text}] — {reason}")
                if self._log_fn:
                    self._log_fn(f"[filtré: {reason}] {frame.text}")
                return
            logger.info(f"VoiceGate: accepted [{frame.text}] ({reason})")

        await self.push_frame(frame, direction)
