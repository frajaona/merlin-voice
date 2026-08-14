# Merlin Voice

Local French voice assistant: Pipecat pipeline (WebRTC ← phone browser) →
Silero VAD → MLX Whisper (fp16 turbo) → Ollama (qwen, `reasoning_effort:"none"`)
→ Kokoro TTS. Public-use hardening in `voice_guard.py` (household speaker gate,
attention gate with activator binding) and `wake_word.py` (raw-audio wake-word
engine). Tool plugins auto-load from `plugins/*.py`.

## Read these before making changes

- `ROADMAP.md` — improvement backlog: done / to-do / known ceilings.
- `docs/DECISIONS.md` — every key decision with rationale and the measured
  data behind it (thresholds, calibrations, incidents, dead ends). **Do not
  re-litigate or "helpfully" retune these without new data.**

## Working rules

- **Every session that changes behavior must update `ROADMAP.md` (done/to-do)
  and, for any decision/calibration/dead-end/lead, `docs/DECISIONS.md`.**
  Project context lives in these files, not in assistant memory.
- User preference (Fred): the voice gate must prefer NOT acting over acting
  wrongly (missed speaker switch beats wrong switch, dropped turn beats
  answering a bystander).
- Never delete or migrate `data/voices/*.npz` from test code — tests must pass
  explicit tmp paths (a module-level path once destroyed the real profile).

## Ops

- Run: `venv/bin/python bot.py` (HTTPS :7860, cert.pem/key.pem).
- Restart: kill by port PID — `kill $(lsof -tnP -iTCP:7860 -sTCP:LISTEN)`,
  wait for the port to free, force-kill if needed, then start. Do NOT
  `pkill -f "python bot.py"` (macOS process name is capital-P `Python`; a
  half-dead process once kept serving stale code).
- Logs: `data/merlin.log` (rotating). Gate decisions: grep `VoiceGate`.
  Wake engine: grep `wake word`; `MERLIN_WAKE_DEBUG=1` logs live partials
  (very verbose — never leave it on).
- Transcripts: `data/transcripts.db` (sqlite, table `turns`); rejected turns
  are stored with a `[filtré: reason]` prefix — downstream consumers must
  filter them; they double as the STT/gate tuning dataset.
- Voice profiles: `tools/voice_profile.py [status|enroll|cancel|reset]`
  (enroll on a complete profile opens a diversity top-up).
- Tests (offline, no bot needed): `venv/bin/python tools/test_voice_guard.py`
  and `tools/test_wake_word.py`.

## Env knobs

See the docstrings of `voice_guard.py` (MERLIN_SPEAKER_*, MERLIN_FAMILY_MODE,
MERLIN_REQUIRE_WAKE, MERLIN_FOLLOWUP/QUESTION_SECS, MERLIN_STT_*) and `bot.py`
(MERLIN_RAW_WAKE, LLM_*). Defaults are the calibrated values from
`docs/DECISIONS.md`.
