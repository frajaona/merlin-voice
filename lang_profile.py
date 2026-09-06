"""Language profiles — everything in the pipeline that depends on the
spoken language, in one place.

One language per session, chosen by the dashboard at connect time (`lang`
field of the /api/offer body) and threaded through run_bot. The `fr`
profile IS the historical configuration: every value here is the constant
that used to be hard-coded, so the French path is unchanged to the byte and
the calibrations in docs/DECISIONS.md still apply. The `en` profile was
measured on synthesized speech on 2026-09-06 (docs/DECISIONS.md, "Mode
anglais") and is meant for a French family learning English: the LLM is
prompted as a generous listener, French words are expected in the Whisper
prompt, and the raw wake channel KEEPS the French zipformer (the only one
that hears "Olympia" pronounced the French way) and ADDS the English one
(English stop words, anglicized pronunciation).

Plugins pick their spoken filler phrases with `phrase(key, params.llm)` —
run_bot stamps the session language on the LLM service object.

Env knobs:
    MERLIN_STOP_WORDS       French transcript stop words ("chut,chute,stop")
    MERLIN_STOP_WORDS_EN    English transcript stop words ("stop,hush,quiet,shut,chut")
    MERLIN_VAD_STOP_SECS_EN silence that ends an English turn (1.0 — learners
                            hesitate; French keeps the calibrated 0.8)
    MERLIN_TTS_VOICE_EN     Kokoro voice for English ("af_heart"; "bf_emma" = British)
    MERLIN_TTS_SPEED_EN     Kokoro speed for English (1.0; 0.9 = slower, untested)
    MERLIN_RAW_WAKE_EN      "0" to run only the French zipformer in English mode
"""
import datetime
import os
from dataclasses import dataclass, field
from typing import Callable

from pipecat.transcriptions.language import Language


def _csv_env(name: str, default: str) -> frozenset:
    return frozenset(
        w.strip() for w in os.getenv(name, default).lower().split(",") if w.strip()
    )


@dataclass(frozen=True)
class LangProfile:
    code: str
    label: str
    # --- STT (Whisper) ---
    whisper_lang: str
    pipecat_language: Language
    stt_prompt: str
    hallucination_markers: tuple
    # --- gate word lists (transcript channel) ---
    wake_exclude: frozenset       # real words starting like the wake word
    stop_words: frozenset
    closer_core: frozenset
    closer_filler: frozenset
    # --- LLM ---
    system_prompt: str            # with a {current_date} placeholder
    date_fn: Callable[[datetime.date], str]
    skill_ready_note: str         # with a {capabilities} placeholder
    # --- TTS (Kokoro) ---
    tts_voice: str
    tts_language: Language
    tts_speed: float
    # --- turn taking ---
    vad_stop_secs: float
    # --- raw-audio wake engines (wake_word.ENGINES keys) ---
    raw_wake_langs: tuple
    # --- spoken filler phrases used by plugins ---
    phrases: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# French — the historical configuration (calibrated values, do not retune
# here without new data; see docs/DECISIONS.md).
# ---------------------------------------------------------------------------

FR_SYSTEM_PROMPT = """Tu es Olympia, une assistante personnelle intelligent et chaleureux. Tu réponds toujours en français et tu tutoies l'utilisateur.

Règles importantes :
- Tes réponses seront lues à voix haute — pas de markdown, pas d'astérisques, pas de puces, pas de symboles spéciaux.
- Phrases courtes et naturelles. Maximum deux phrases par réponse sauf si on te demande des détails.
- Réponds de façon conversationnelle, comme si tu parlais à quelqu'un en face de toi.
- Ne termine jamais ta réponse par une question de politesse (« Tu veux autre chose ? », « Veux-tu que je change quelque chose ? »). Pose une question uniquement s'il te manque une information indispensable pour agir.
- Ne dis jamais "En tant qu'IA..." ou "Je suis un assistant...".
- N'annonce jamais une action comme effectuée si tu n'as pas d'outil pour la faire réellement.

Nous sommes le {current_date}. Tiens-en compte pour juger de la fraîcheur des informations.

Tu disposes d'un outil web_search pour chercher sur internet. Utilise-le dès que la question porte sur des informations actuelles ou vérifiables : météo, actualités, horaires, prix, résultats sportifs, faits récents. Pour les actualités, utilise type "news". N'invente jamais une information datée — cherche. Ignore les résultats trop anciens par rapport à la question. Après une recherche, réponds en une ou deux phrases avec l'essentiel, sans citer les adresses des sites.

Fabrication de nouveaux outils — le déroulé est toujours le même :
1. Si l'utilisateur demande une action que tu ne sais pas encore faire, appelle IMMÉDIATEMENT request_feature — ne dis jamais « je note ta demande » sans avoir réellement appelé cet outil. Ensuite dis-le honnêtement et propose de fabriquer l'outil (quelques minutes).
2. N'appelle build_skill que si l'utilisateur confirme explicitement la fabrication.
3. Si l'utilisateur demande où en est la fabrication, appelle workshop_status.
4. Quand un outil terminé attend l'activation, décris-le brièvement et n'appelle approve_skill que si l'utilisateur confirme explicitement l'activation.
Ne lance jamais une fabrication ni une activation sans confirmation.
"""

FR_SKILL_READY_NOTE = (
    "\nNote interne : de nouveaux outils ont été fabriqués et attendent une approbation "
    "avant activation : {capabilities}. Mentionne-le brièvement au début de la "
    "conversation, une seule fois. Si Fred confirme vouloir l'activer, appelle "
    "l'outil approve_skill avec le slug correspondant."
)


def _date_fr(now: datetime.date) -> str:
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
              "août", "septembre", "octobre", "novembre", "décembre"]
    days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{days[now.weekday()]} {now.day} {months[now.month - 1]} {now.year}"


FR = LangProfile(
    code="fr",
    label="Français",
    whisper_lang="fr",
    pipecat_language=Language.FR,
    stt_prompt=(
        "Discussion en français avec Olympia, un assistant vocal. "
        "Météo, minuteur, actualités, l'Île d'Yeu, La Rochelle, Bordeaux."
    ),
    hallucination_markers=(
        "sous titrage",
        "sous titres",
        "amara org",
        "abonnez vous",
        "merci d avoir regarde",
        "n oubliez pas de vous abonner",
    ),
    # Real French words that start like the wake word — never wake on these.
    # ("Olympe de Gouges" was once transcribed "Olympia de Gouges" under the
    # prompt bias — accepted: rare, and the speaker gate still applies.)
    wake_exclude=frozenset({
        "olympe", "olympes", "olympien", "olympiens", "olympienne", "olympiennes",
        "olympique", "olympiques", "olympisme", "olympiade", "olympiades",
    }),
    # Exact-word match, not prefix: "stoppe la musique" or "parachute" must
    # not stop the session. "chute" is in the default because Whisper
    # transcribes the interjection "Chut !" as "chute." (measured).
    stop_words=_csv_env("MERLIN_STOP_WORDS", "chut,chute,stop"),
    # Clôture polie : cœurs volontairement limités aux remerciements/adieux —
    # PAS « ok »/« d'accord »/« oui », réponses légitimes aux questions du bot.
    closer_core=frozenset(("merci", "revoir", "bientot", "adieu")),
    closer_filler=frozenset((
        "olympia", "beaucoup", "bien", "tres", "c", "est", "gentil", "super",
        "parfait", "nickel", "top", "cool", "a", "au", "la", "le", "prochaine",
        "bon", "bonne", "nuit", "journee", "soiree", "et", "ca", "va", "d",
        "accord", "ok",
    )),
    system_prompt=FR_SYSTEM_PROMPT,
    date_fn=_date_fr,
    skill_ready_note=FR_SKILL_READY_NOTE,
    tts_voice=os.getenv("TTS_VOICE", "ff_siwis"),
    tts_language=Language.FR,
    tts_speed=1.0,
    vad_stop_secs=0.8,
    raw_wake_langs=("fr",),
    phrases={
        "looking": "Je regarde ça.",
        "launching": "Je lance ça.",
        "timer_done": "C'est l'heure ! Le minuteur est terminé.",
    },
)


# ---------------------------------------------------------------------------
# English — for a French household learning English (2026-09-06).
# ---------------------------------------------------------------------------

EN_SYSTEM_PROMPT = """You are Olympia, a warm and clever personal assistant for a French family. You always answer in English.

The family is learning English. They are not native speakers: their English has mistakes, their accent is French, French words slip in (météo = weather, minuteur = timer, cuisine = kitchen, salon = living room, chambre = bedroom, volets = shutters, lumière = light), and the speech-to-text sometimes garbles words. Be a generous listener:
- Work out the most likely intent from context and act on it. Never comment on grammar, vocabulary or pronunciation unless someone explicitly asks to be corrected or asks how to say something.
- If a request is truly ambiguous, ask ONE short question. If you did not understand at all, say so simply and ask them to say it again.
- Use simple, everyday English (A2/B1 level): common words, short sentences, no idioms or slang.

Important rules:
- Your answers are read aloud — no markdown, no asterisks, no bullet points, no special symbols.
- Short, natural sentences. At most two sentences per answer unless you are asked for details.
- Speak conversationally, as if talking to someone in front of you.
- Never end an answer with a politeness question ("Anything else?", "Do you want me to change something?"). Ask a question only when you are missing information you need to act.
- Never say "As an AI..." or "I am an assistant...".
- Never claim an action is done if you have no tool to actually do it — always call the tool first.

Today is {current_date}. Take it into account when judging how fresh information is.

You have a web_search tool to search the internet. Use it whenever the question is about current or verifiable information: weather, news, opening hours, prices, sports results, recent facts. For news, use type "news". Never invent dated information — search. Ignore results that are too old for the question. After a search, answer in one or two sentences with the essentials, without reading out website addresses.

Building new tools — the flow is always the same:
1. If the user asks for something you cannot do yet, IMMEDIATELY call request_feature — never say "I noted your request" without actually calling this tool. Then say so honestly and offer to build the tool (a few minutes).
2. Only call build_skill if the user explicitly confirms the build.
3. If the user asks how the build is going, call workshop_status.
4. When a finished tool is waiting for activation, describe it briefly and only call approve_skill if the user explicitly confirms the activation.
Never start a build or an activation without confirmation.
"""

EN_SKILL_READY_NOTE = (
    "\nInternal note: new tools have been built and are waiting for approval "
    "before activation: {capabilities}. Mention it briefly at the start of the "
    "conversation, once. If Fred confirms he wants it activated, call the "
    "approve_skill tool with the matching slug."
)


def _date_en(now: datetime.date) -> str:
    return now.strftime("%A %-d %B %Y")


EN = LangProfile(
    code="en",
    label="English",
    whisper_lang="en",
    pipecat_language=Language.EN,
    # Deliberately WITHOUT the name: with "Olympia" in the prompt Whisper
    # heard "a limpid pool" as "Olympia pool" 3/4 (false wake on the
    # transcript channel); without it the name is still spelled right 12/12.
    # The French words are the ones the family will mix in — the prompt
    # fixed "météo"/"minuteur" 4/4 (measured 2026-09-06).
    stt_prompt=(
        "A French family practising English with a voice assistant. "
        "Weather, timer, music, news, lights. They sometimes mix in French words: "
        "météo, minuteur, cuisine, salon, chambre, volets, lumière, "
        "l'Île d'Yeu, La Rochelle, Bordeaux."
    ),
    hallucination_markers=(
        "thank you for watching",
        "thanks for watching",
        "amara org",
        "please subscribe",
        "don t forget to subscribe",
        "subtitles by",
    ),
    # "Olympian" contains "olympia" and is NOT covered by the French list;
    # "Olympus"/"Olympic" share the prefix.
    wake_exclude=frozenset({
        "olympian", "olympians", "olympus", "olympic", "olympics",
        "olympiad", "olympiads", "olympe",
    }),
    # "hush" decoded HUSH 4/4 on both channels; "shut" covers "shut up";
    # "chut" stays for the family habit (the French zipformer hears it on
    # the raw channel anyway).
    stop_words=_csv_env("MERLIN_STOP_WORDS_EN", "stop,hush,quiet,shut,chut"),
    closer_core=frozenset(("thanks", "thank", "bye", "goodbye", "goodnight")),
    closer_filler=frozenset((
        "olympia", "you", "so", "much", "very", "a", "lot", "that", "s", "all",
        "ok", "okay", "cool", "great", "perfect", "nice", "good", "night", "see",
        "later", "and", "have", "day", "evening", "too",
    )),
    system_prompt=EN_SYSTEM_PROMPT,
    date_fn=_date_en,
    skill_ready_note=EN_SKILL_READY_NOTE,
    tts_voice=os.getenv("MERLIN_TTS_VOICE_EN", "af_heart"),
    tts_language=Language.EN_GB if os.getenv("MERLIN_TTS_VOICE_EN", "").startswith("b") else Language.EN,
    tts_speed=float(os.getenv("MERLIN_TTS_SPEED_EN", "1.0")),
    # Learners hesitate mid-sentence; 0.8 s split "Olympia, can you… tell
    # me" into two turns. Costs +0.2 s of latency per turn, English only.
    vad_stop_secs=float(os.getenv("MERLIN_VAD_STOP_SECS_EN", "1.0")),
    # French zipformer FIRST: it is the one that hears "Olympia" said the
    # French way (20/20; the English one decoded it as "Pierre" 0/24). The
    # English zipformer adds the raw stop channel in English ("stop",
    # "hush": the French one decodes STAPE / nothing) and the anglicized
    # pronunciation of the name.
    raw_wake_langs=("fr", "en") if os.getenv("MERLIN_RAW_WAKE_EN", "1").lower() not in ("0", "off", "false") else ("fr",),
    phrases={
        "looking": "Let me check.",
        "launching": "Starting that now.",
        "timer_done": "Time's up! The timer is finished.",
    },
)

PROFILES = {"fr": FR, "en": EN}
DEFAULT = FR


def get(code: str | None) -> LangProfile:
    """Profile for a language code; unknown/missing → French (never fail a
    connection over a bad flag)."""
    return PROFILES.get((code or "").lower().strip(), DEFAULT)


def phrase(key: str, llm=None) -> str:
    """Spoken filler for a plugin, in the session's language. run_bot stamps
    `merlin_lang` on the LLM service; anything else (tests, CLI) is French."""
    profile = get(getattr(llm, "merlin_lang", None))
    return profile.phrases.get(key) or FR.phrases[key]
