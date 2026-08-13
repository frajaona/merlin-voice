"""
Merlin Voice — conversational voice AI pipeline
Pipecat + MLX Whisper + local LLM (Ollama direct, Hermes via env override) + Kokoro TTS
"""
import asyncio
import datetime
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.services.kokoro.tts import KokoroTTSService
import pipecat.services.kokoro.tts as _kokoro_tts_module
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection, IceServer
from pipecat.transcriptions.language import Language

load_dotenv(override=True)

# Fix: bundled espeak-ng uses "fr-fr" not bare "fr" — patch the mapping function
_original_lang_fn = _kokoro_tts_module.language_to_kokoro_language
def _patched_lang_fn(language):
    code = _original_lang_fn(language)
    return "fr-fr" if code == "fr" else code
_kokoro_tts_module.language_to_kokoro_language = _patched_lang_fn

# Fix: RawAudioTrack paces against a clock anchored at track creation. When the
# event loop stalls (Kokoro ONNX synthesis blocks it per sentence), the track
# falls behind schedule and then "catches up" by sending queued speech faster
# than real time. The browser's jitter buffer overflows and drops the tail of
# the answer (heard as answers being cut off). Re-anchor the clock after a
# stall instead of bursting.
import fractions as _fractions
import time as _time
import numpy as _np
from av import AudioFrame as _AudioFrame
import pipecat.transports.smallwebrtc.transport as _webrtc_transport_module

async def _paced_recv(self):
    if self._timestamp > 0:
        wait = self._start + (self._timestamp / self._sample_rate) - _time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        elif wait < -0.1:
            # Fell behind schedule — re-anchor so playout resumes at real time
            # rather than bursting the backlog.
            self._start = _time.time() - (self._timestamp / self._sample_rate)

    if not self._chunk_queue:
        if self._auto_silence:
            chunk, future = bytes(self._bytes_per_10ms), None
        else:
            while not self._chunk_queue:
                await asyncio.sleep(0.005)
            chunk, future = self._chunk_queue.popleft()
    else:
        chunk, future = self._chunk_queue.popleft()
    if future and not future.done():
        future.set_result(True)

    samples = _np.frombuffer(chunk, dtype=_np.int16)
    frame = _AudioFrame.from_ndarray(samples[None, :], layout="mono")
    frame.sample_rate = self._sample_rate
    frame.pts = self._timestamp
    frame.time_base = _fractions.Fraction(1, self._sample_rate)
    self._timestamp += self._samples_per_10ms
    return frame

_webrtc_transport_module.RawAudioTrack.recv = _paced_recv

# LLM backend — direct Ollama by default (lowest latency in the voice hot path).
# To route through Hermes instead, set:
#   LLM_BASE_URL=http://127.0.0.1:8642/v1  LLM_MODEL=default  LLM_API_KEY=<hermes key>  LLM_REASONING_EFFORT=
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.6:35b-a3b-q4_K_M")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
# Qwen3 thinking mode adds 15-50s of silent reasoning before the first spoken
# token — must stay off in the voice hot path. Empty string = don't send.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")
TTS_VOICE = os.getenv("TTS_VOICE", "ff_siwis")

SYSTEM_PROMPT = """Tu es Merlin, un assistant personnel intelligent et chaleureux. Tu réponds toujours en français et tu tutoies l'utilisateur.

Règles importantes :
- Tes réponses seront lues à voix haute — pas de markdown, pas d'astérisques, pas de puces, pas de symboles spéciaux.
- Phrases courtes et naturelles. Maximum deux phrases par réponse sauf si on te demande des détails.
- Réponds de façon conversationnelle, comme si tu parlais à quelqu'un en face de toi.
- Ne dis jamais "En tant qu'IA..." ou "Je suis un assistant...".
- N'annonce jamais une action comme effectuée si tu n'as pas d'outil pour la faire réellement.

Nous sommes le {current_date}. Tiens-en compte pour juger de la fraîcheur des informations.

Tu disposes d'un outil web_search pour chercher sur internet. Utilise-le dès que la question porte sur des informations actuelles ou vérifiables : météo, actualités, horaires, prix, résultats sportifs, faits récents. Pour les actualités, utilise type "news". N'invente jamais une information datée — cherche. Ignore les résultats trop anciens par rapport à la question. Après une recherche, réponds en une ou deux phrases avec l'essentiel, sans citer les adresses des sites.

Si l'utilisateur demande une action que tu ne sais pas encore faire (contrôler la maison, minuteur, musique, agenda...), appelle l'outil request_feature puis dis-le honnêtement : tu ne sais pas encore le faire, mais la demande est notée et cette capacité sera ajoutée.
"""


def _system_prompt() -> str:
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre"]
    days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    now = datetime.date.today()
    date_fr = f"{days[now.weekday()]} {now.day} {months[now.month - 1]} {now.year}"
    return SYSTEM_PROMPT.format(current_date=date_fr) + _skill_ready_note()


def _skill_ready_note() -> str:
    """One-time note about freshly built skill candidates awaiting approval."""
    import json
    ready_file = Path(__file__).resolve().parent / "data" / "skill-ready.jsonl"
    if not ready_file.exists():
        return ""
    entries = [json.loads(l) for l in ready_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    fresh = [e for e in entries if not e.get("announced")]
    if not fresh:
        return ""
    for e in fresh:
        e["announced"] = True
    ready_file.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8"
    )
    capabilities = ", ".join(f"« {e['capability']} » (candidats/{e['slug']})" for e in fresh)
    return (
        "\nNote interne : de nouveaux outils ont été fabriqués et attendent l'approbation "
        f"de Fred avant activation : {capabilities}. Mentionne-le brièvement au début de "
        "la conversation, une seule fois."
    )

# Tool plugins: every plugins/*.py exporting SCHEMA + handler is auto-loaded.
# New capabilities are added there, never wired here (see plugins/README.md).
def load_plugins() -> dict:
    import importlib.util
    from pathlib import Path

    plugins = {}
    for path in sorted((Path(__file__).resolve().parent / "plugins").glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"merlin_plugins.{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            schema, handler = module.SCHEMA, module.handler
        except Exception as e:
            logger.error(f"plugin {path.name} failed to load, skipping: {e}")
            continue
        if schema.name in plugins:
            logger.error(f"plugin {path.name}: duplicate tool name '{schema.name}', skipping")
            continue
        plugins[schema.name] = module
        logger.info(f"plugin loaded: {schema.name} ({path.name})")
    return plugins


PLUGINS = load_plugins()

from transcript_store import TranscriptStore

TRANSCRIPTS = TranscriptStore()


class UserTranscriptLogger(FrameProcessor):
    """Passthrough processor that records every final user transcription.

    Logs raw STT output (even turns later discarded as echo or noise) — the
    misrecognitions are exactly what the STT test set needs.
    """

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await asyncio.to_thread(TRANSCRIPTS.append, self._session_id, "user", frame.text)
        await self.push_frame(frame, direction)

pcs_map: Dict[str, SmallWebRTCConnection] = {}

# No STUN needed for local LAN — host candidates only
ice_servers = []


def _normalize_words(text: str) -> list:
    import unicodedata
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return [w for w in "".join(c if c.isalnum() else " " for c in text).split() if w]


class RecentTTSWords:
    """Words the bot spoke in the last `window_secs` — the echo reference."""

    def __init__(self, window_secs: float = 15.0):
        from collections import deque as _deque
        self._entries = _deque()
        self._window = window_secs

    def add(self, text: str):
        self._entries.append((_time.time(), set(_normalize_words(text))))

    def __contains__(self, word: str) -> bool:
        now = _time.time()
        while self._entries and now - self._entries[0][0] > self._window:
            self._entries.popleft()
        return any(word in words for _, words in self._entries)


class EchoAwareMinWordsStrategy(MinWordsUserTurnStartStrategy):
    """Min-words barge-in that ignores the bot's own voice echoing back.

    If the phone's echo cancellation fails, the mic hears the bot and Whisper
    transcribes the bot's own sentence as 'user speech', which would interrupt
    the answer. While the bot is speaking, an interrupting transcription whose
    words mostly appear in the bot's recent TTS output is treated as echo and
    dropped.
    """

    def __init__(self, *, recent_tts_words: "RecentTTSWords", echo_overlap: float = 0.7, **kwargs):
        super().__init__(**kwargs)
        self._recent_tts_words = recent_tts_words
        self._echo_overlap = echo_overlap

    async def _handle_transcription(self, frame):
        if self._bot_speaking:
            words = _normalize_words(frame.text)
            if words:
                overlap = sum(1 for w in words if w in self._recent_tts_words) / len(words)
                if overlap >= self._echo_overlap:
                    logger.info(
                        f"Ignoring echo of bot's own speech (overlap={overlap:.0%}): [{frame.text}]"
                    )
                    return ProcessFrameResult.CONTINUE
        return await super()._handle_transcription(frame)


async def run_bot(webrtc_connection: SmallWebRTCConnection):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                stop_secs=0.8,
                start_secs=0.1,
                confidence=0.3,
            )
        )
    )

    stt = WhisperSTTServiceMLX(
        model=MLXModel.LARGE_V3_TURBO_Q4,
        language="fr",
    )

    llm = OpenAILLMService(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        settings=OpenAILLMService.Settings(
            extra={"reasoning_effort": LLM_REASONING_EFFORT} if LLM_REASONING_EFFORT else {},
        ),
    )

    tts = KokoroTTSService(
        voice_id=TTS_VOICE,
        settings=KokoroTTSService.Settings(language=Language.FR),
    )

    session_id = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    logger.info(f"session started: {session_id}")

    # Track what the bot says: for barge-in echo detection, and for the
    # transcript log. Sentences are logged when synthesized — a barge-in can
    # cut playback, so the tail of a logged answer may not have been heard.
    recent_tts_words = RecentTTSWords()
    _orig_run_tts = tts.run_tts

    async def _run_tts_tracking(text: str, context_id: str):
        recent_tts_words.add(text)
        await asyncio.to_thread(TRANSCRIPTS.append, session_id, "assistant", text)
        async for frame in _orig_run_tts(text, context_id):
            yield frame

    tts.run_tts = _run_tts_tracking

    for tool_name, module in PLUGINS.items():
        llm.register_function(tool_name, module.handler)

    messages = [{"role": "system", "content": _system_prompt()}]
    context = LLMContext(
        messages=messages,
        tools=ToolsSchema(standard_tools=[m.SCHEMA for m in PLUGINS.values()]),
    )
    # Barge-in requires a real transcription of >= 3 words while the bot is
    # speaking — a bare VAD blip (breath, noise, speaker echo) no longer cuts
    # the answer — and transcriptions that are mostly the bot's own recent words
    # are treated as echo and ignored. When the bot is silent, a single
    # transcribed word starts the turn.
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[EchoAwareMinWordsStrategy(min_words=3, recent_tts_words=recent_tts_words)],
            ),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        vad,
        stt,
        UserTranscriptLogger(session_id),
        aggregators.user(),
        llm,
        tts,
        transport.output(),
        aggregators.assistant(),
    ])

    worker = PipelineWorker(pipeline, enable_rtvi=False)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, connection):
        logger.info("Client connected — pipeline active")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, connection):
        logger.info("Client disconnected — stopping pipeline")
        await worker.cancel()

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    pc_id = request.get("pc_id")

    if pc_id and pc_id in pcs_map:
        connection = pcs_map[pc_id]
        logger.info(f"Renegotiating connection {pc_id}")
        await connection.renegotiate(
            sdp=request["sdp"],
            type=request["type"],
            restart_pc=request.get("restart_pc", False),
        )
    else:
        connection = SmallWebRTCConnection(ice_servers)
        await connection.initialize(sdp=request["sdp"], type=request["type"])

        @connection.event_handler("closed")
        async def on_closed(conn: SmallWebRTCConnection):
            pcs_map.pop(conn.pc_id, None)
            logger.info(f"Connection {conn.pc_id} closed")

        background_tasks.add_task(run_bot, connection)

    answer = connection.get_answer()
    pcs_map[answer["pc_id"]] = connection
    return answer


@app.get("/api/health")
async def health():
    return {"status": "ok", "connections": len(pcs_map)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    coros = [pc.disconnect() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()

    ssl_cert = Path("cert.pem")
    ssl_key  = Path("key.pem")

    if ssl_cert.exists() and ssl_key.exists():
        logger.info("Starting with HTTPS (cert.pem / key.pem)")
        uvicorn.run(app, host=args.host, port=args.port,
                    ssl_certfile=str(ssl_cert), ssl_keyfile=str(ssl_key))
    else:
        logger.warning("No cert.pem found — starting HTTP (mic blocked on remote devices)")
        uvicorn.run(app, host=args.host, port=args.port)
