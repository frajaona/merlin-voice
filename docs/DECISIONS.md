# Journal des décisions

Chaque décision clé, avec sa justification et les données mesurées derrière.
Ne pas modifier une valeur calibrée sans nouvelles données — les logs et
`data/transcripts.db` sont la source pour re-calibrer.

## 2026-08-12/13 — Bring-up (décisions d'architecture initiales)

- **Ollama direct dans le chemin chaud, pas Hermes.** Mesuré : Hermes ~1,7 s
  TTFT contre ~0,25 s en direct. Hermes (localhost:8642, OpenAI-compat, clé
  requise) reste joignable via `LLM_BASE_URL` ; rôle futur = worker de fond
  (`delegate()`), jamais le chemin chaud.
- **`reasoning_effort: "none"` obligatoire.** Le mode thinking de Qwen3 est
  actif par défaut et brûle 15–50 s de tokens avant le premier mot parlé.
  `think:false` et `/no_think` sont IGNORÉS par l'endpoint OpenAI d'Ollama —
  seul `reasoning_effort` marche. Avec ça, le 35B-A3B (MoE, ~3,3 B actifs)
  répond aussi vite que le 9B → un seul modèle, pas de routage à deux étages.
- **Routage par auto-score de confiance : rejeté.** Signal non calibré, et
  l'enveloppe JSON requise casse le streaming TTS. L'escalade se fait par
  appel d'outil (web_search, futur delegate/home_assistant).
- **Bugs Pipecat/WebRTC du bring-up** (tous corrigés, sondes dans `tools/`) :
  `muted="false"` (attribut booléen HTML = muet quoi qu'il arrive) ; pacing en
  rafale après un stall de l'event loop (monkeypatch re-ancrage de
  `RawAudioTrack`) ; auto-interruption par écho (barge-in exige ≥3 mots
  transcrits + filtre des mots récents du TTS) ; keep-alive : Pipecat coupe
  l'audio sortant en silence si >3 s sans message datachannel — le client
  ping à 1 s (`static/index.html`). Les deux derniers méritent des issues
  upstream (non déposées).
- **web_search** via `ddgs` (DuckDuckGo, sans clé API, region fr-fr) ;
  « Je regarde ça » pendant la recherche en thread.
- **Revue d'architecture complète du 13/08** (artifact « The Merlin Review »,
  lien dans la mémoire assistant / historique claude.ai) : verdicts intégrés
  ici et dans `ROADMAP.md` ; le P0 (prompt promettant domotique et mémoire
  inexistantes → actions hallucinées) est corrigé, le prompt interdit
  désormais d'annoncer une action sans outil.

## 2026-08-13 — Garde vocale

- **VAD confidence 0.3 → 0.6** (`bot.py`). 0.3 laissait passer souffles et
  bruits vers Whisper, qui hallucinait des tours entiers (« Merci. »,
  « Sous-titrage ST' 501 »). Mesuré : Whisper transcrit du bruit gaussien pur
  en « Merci. » avec no_speech_prob=0.00 — les filtres de probabilité ne
  peuvent PAS l'attraper ; il faut empêcher le bruit d'atteindre Whisper.
  Plancher RMS 0.0035 en seconde ligne.
- **Whisper turbo Q4 → fp16**. Mesuré : même latence (~0,1 s par énoncé sur
  M-series), meilleure précision. Le passage à fp16 est gratuit.
- **Décodage Whisper** : `condition_on_previous_text=False` (boucles),
  `initial_prompt` avec vocabulaire du domaine (+ `data/stt_vocab.txt`),
  filtre par segment (no_speech > 0.55, avg_logprob < −1.1), liste noire des
  hallucinations françaises connues, détection de boucles de répétition.
- **Empreinte locuteur : CAM++ VoxCeleb via sherpa-onnx** (~10 ms/énoncé,
  ONNX, pas de torch au runtime). Embeddings cosinus, profil = moyenne.
- **Seuil locuteur 0.60** (`MERLIN_SPEAKER_THRESHOLD`). Calibré sur données
  réelles du 13/08 : Fred 0.72–0.89 contre son profil ; sa femme sur le même
  téléphone 0.08–0.54. Le seuil initial 0.45 a laissé passer une question à
  0.54. 0.60 coupe la marge en deux.
- **ADAPT_SIM 0.75** (auto-enrichissement du profil). À 0.55, une session
  mixte a contaminé le profil avec 7 embeddings potentiellement de la femme
  (profil restauré en tronquant aux 9 premiers). L'adaptation doit rester
  nettement au-dessus du seuil d'acceptation.
- **Énoncés courts** : `VERIFY_MIN_SECS=1.0` ET `VERIFY_MIN_WORDS=3`. La durée
  seule ne suffit pas : le segment inclut ~1 s de tampon VAD, un « Non. » réel
  mesurait 1,9 s et scorait 0.08 contre le propre profil du locuteur. Les
  énoncés courts passent sans vérification (les confirmations oui/non doivent
  marcher) — la fenêtre d'attention les couvre.

## 2026-08-13 — Attention et activateur

- **Mot d'éveil « Merlin » requis pour ouvrir un échange** ; fenêtre de suivi
  12 s après la réponse (30 s si le bot a posé une question). Motivé par les
  transcriptions réelles : conversations parallèles répondues par le bot.
- **Profils par personne** (`data/voices/<nom>.npz`), inscription passive en
  8 énoncés, top-up de diversité sur profil complet (`enroll` sur un profil
  complet vise count+8). Garde anti-contamination : pas d'inscription si
  sim < 0.30 contre le profil partiel (dès 2 embeddings).
- **Liaison à l'activateur** : la voix qui éveille possède l'échange ; les
  autres voix (même inscrites) sont rejetées jusqu'à un nouveau « Merlin… ».
  Ancre = embeddings de l'échange en cours (même micro/pièce, plus fiable que
  le profil stocké) ; continuation acceptée si profil OU ancre ≥ seuil.
- **Préférence utilisateur (ferme)** : rater un passage de micro vaut mieux
  qu'un passage erroné. Pas de re-liaison indulgente sur énoncé court : la
  barre 0.35 (SHORT_WAKE_SIM) ne vaut que pour OUVRIR un échange, jamais pour
  voler le micro. Récupération : répéter avec une phrase complète.
- **Adaptation confirmée par l'ancre** (14/08) : ancre ≥ 0.80 et profil ≥ 0.45
  → enrichit le profil. C'est ainsi que le profil apprend la voix lointaine et
  douce (un vrai énoncé « marées » à 4 s a scoré 0.37 → rejeté à tort, cause :
  profil mono-session).

## 2026-08-14 — Moteur d'éveil audio brut

- **Impasse : modèles KWS anglais** (zipformer gigaspeech 3.3M). Mesuré : ils
  entendent « Merlin » prononcé à la française comme une soupe de tokens
  différente à chaque fois (MELA/SELEN/MITTLEN/MALLA) — aucun motif stable.
  Ne pas réessayer sans modèle entraîné sur du français.
- **Solution : ASR streaming français** (sherpa-onnx zipformer CommonVoice,
  int8, ~130 Mo, ~30× temps réel sur un cœur) décode tout l'audio micro sur
  un thread dédié ; regex floue `m[ae]{1,2}rl[iy]` sur le texte sans espaces.
  Décodages réels observés : MERLIN, SALUMEERLIN (« Salut Merlin » collé),
  MERLINGUE (« Merlin ? »), variantes en a. merlan/merlot/merle continuent
  par a/o/e après le l → silencieux. « Berlin » : pas de m → silencieux.
- **Dernier mot d'un partiel jugé seulement à l'endpoint** : un « MERL » coupé
  peut encore devenir « merlan » (faux éveil mesuré avant cette règle).
- **« Merlin ? » nu délégué au canal Whisper** : le décodage streaming en est
  instable (SMERLAND…) ; Whisper le transcrit bien. Les deux canaux sont en OU.
- Un faux éveil est peu coûteux : il n'ouvre que la fenêtre — la voix doit
  encore correspondre à un profil inscrit pour être répondue.

## 2026-08-14 — Bench NVFP4 (moteur MLX) vs q4_K_M, et température

Contexte : le blog Ollama (06/26) annonce le moteur MLX + quant NVFP4
(« moitié de la perte de qualité du q4_K_M, ~20 % plus rapide, snapshots
multi-tours »). Testé `qwen3.6:35b-a3b-nvfp4` (24 Go, comme q4_K_M) en
simulant le chemin chaud exact (streaming, `reasoning_effort:"none"`,
prompt Merlin, outil web_search). Scripts : jobs Claude `bench_nvfp4.py`,
`bench_toolrepeat.py`, `bench_temp.py`.

- **Latence : nvfp4 gagne nettement.** TTFT médian 0,09 s contre 0,33 s
  (q4_K_M), décodage équivalent (~100–105 tok/s). Le gain vient du moteur
  MLX et de ses snapshots.
- **Découverte principale — la température, pas le quant.** bot.py ne
  fixait aucune température → défaut de la fiche modèle = 1.0. À temp 1,
  les DEUX quants sautent web_search ~40 % du temps sur les questions
  d'actualité (« Je n'ai pas accès aux résultats… » sans chercher — le
  comportement interdit par le prompt). À **température 0,2** : q4_K_M
  14/15 appels corrects, 0 appel parasite sur les pièges.
- **Régression tool-call de nvfp4 mise à nue par la basse température** :
  9/15, dont un échec déterministe 0/5 (« Qui a gagné le match de foot
  hier soir ? » → refus systématique sans outil ; q4_K_M : 4/5). Le quant
  change le comportement, pas seulement la « qualité ».
- **Décision : rester sur q4_K_M** — 0,24 s de TTFT gagnés sont
  imperceptibles dans ~1,5 s de latence bout-en-bout, une régression
  d'outil ne l'est pas (préférence ferme : ne pas agir à tort). Fixer la
  température basse dans bot.py est LE vrai gain de fiabilité. nvfp4
  reste sur disque ; re-tester à la prochaine release Ollama et dans le
  harnais A/B français (ton à basse température à vérifier à l'oreille).

## 2026-08-15 — Bench des challengers LLM

Protocole identique au bench NVFP4 (chemin chaud simulé, temp 0,2, prompt
Merlin, 6 questions de latence + 18 cas web_search). TTFT médian / décodage /
score outils :

- **qwen3.6:35b-a3b-q4_K_M (titulaire)** : 0,30 s / 110 tok/s / **18/18** —
  confirme qu'à temp 0,2 le tool-calling est fiable à 100 % sur ce jeu.
- **mistral-small3.2** : 0,27 s / 36 tok/s / **18/18** — seul challenger
  qualifié. Français naturel et concis, tutoiement stable. Les 36 tok/s du
  dense dépassent largement la vitesse de parole du TTS (~3 mots/s) : pas un
  problème en voix.
- **nemotron-3.5-lightning** : 0,29 s / 112 tok/s / 16/18 — **éliminé du
  chemin chaud pour cause** : INVENTE la météo de demain au lieu de chercher
  (2/2, « ensoleillé avec des températures… » fabriqué), vouvoie dès le
  premier tour, ignore la règle des deux phrases. À reconsidérer comme worker
  de fond (contexte 1M, décodage rapide, taillé agent).
- **muse-glimmer** : TTFT 2,4–5,0 s / 27 tok/s / 16/18 — **éliminé pour la
  voix** (latence, probablement l'encodeur multimodal). Français correct, ses
  « échecs » outils étaient des demandes de précision raisonnables. Piste
  future : caméra / tâches de fond multimodales.
- **gemma4:26b** : **éliminé** — le thinking n'est pas désactivable via
  l'endpoint OpenAI d'Ollama (`reasoning_effort` rejeté) → 300–600 chunks de
  raisonnement par réponse, TTFT 5,7–9,7 s ; outils 10/18 (oublie
  `type:"news"`, demande la ville au lieu de chercher).

**Décision : finale qwen vs mistral-small3.2, à trancher à l'oreille** (A/B
en usage réel, swap `LLM_MODEL=mistral-small3.2` — vérifier au passage son
comportement en contexte long, 32 K seulement sur le tag de base). Les
réponses françaises complètes des cinq modèles sont dans l'artifact de
session et le tmp du job Claude (`challenger_report.md`).

## 2026-08-15 — Seconde vague : variantes MLX et nouveaux tags

Même protocole. Questions posées : MLX sauve-t-il muse-glimmer ? qwen3.8:27b
(nouvelle génération, dense+vision) ? gemma4:12b plus raisonnable que le 26b ?

- **muse-glimmer:30b-nvfp4-dflash** (sa variante la plus rapide possible) :
  TTFT médian 2,29 s contre 2,94 s en GGUF. Même le meilleur cas reste ~8×
  trop lent pour la voix → **élimination définitive**, inutile de tester les
  variantes intermédiaires.
- **qwen3.8:27b** : GGUF 0,95 s / 23 tok/s / 14/18 ; **MLX 0,51 s / 23 tok/s
  / 16/18**. `reasoning_effort` fonctionne (même famille). Dominé par les
  deux finalistes sur tous les axes ; interroge (« quelle ville ? », « quel
  match ? ») au lieu de chercher. Notable : le quant MLX n'a PAS régressé en
  outils ici — la leçon nvfp4 du 14/08 est propre au couple modèle/quant,
  pas au moteur MLX.
- **gemma4:12b-mlx** : surprise, `reasoning_effort:"none"` est ACCEPTÉ sur ce
  build (contrairement au 26b) → TTFT 0,23 s, 45 tok/s, zéro fuite de
  raisonnement — le plus rapide des challengers. Mais **outils 10/18**, même
  pattern que le 26b (oublie `type:"news"`, demande la ville/le match au
  lieu de chercher). Rapide mais indiscipliné : éliminé.
- **La finale reste qwen3.6:35b-a3b vs mistral-small3.2.** À surveiller : un
  éventuel MoE de la génération qwen3.8 (seul le 27b dense existe à ce jour).

## 2026-08-15 — Verdict A/B en production : qwen confirmé, mistral recalé

- **Mesuré sur usage réel** (log, LLM → première phrase TTS) : qwen médiane
  0,49 s sur 51 tours ; mistral-small3.2 médiane 1,54 s, p90 1,94 s sur 18
  tours, et ça croît avec le contexte (1,3 s à 5,5 k chars d'historique,
  2,0 s à 12 k). Ressenti utilisateur confirmé après quelques minutes.
- **Cause structurelle, pas réglable** : prefill dense 24B payé sur TOUT le
  contexte à chaque tour + décodage 3× plus lent jusqu'à la fin de la
  première phrase. Le bench mono-tour à contexte court (TTFT 0,27 s) ne
  pouvait pas le voir. **Leçon de protocole : tout futur bench LLM vocal
  doit inclure un tour à contexte long (~10 k chars).** Le MoE à 3,3 B
  actifs est structurellement le bon profil pour ce chemin chaud.
- **Éviction Ollama corrigée** : les p90 6–7 s de qwen étaient tous des
  premiers tours de session (rechargement après ~5 min d'idle). Fix :
  `_preload_llm()` au démarrage du bot épingle le modèle en mémoire
  (`keep_alive:-1` via l'endpoint NATIF `/api/generate` — l'endpoint
  OpenAI-compat traduit mal -1 en TTL 2 h ; vérifié).
- **Ménage disque** (~94 Go) : supprimés mistral-small3.2, gemma4:26b,
  gemma4:12b-mlx, qwen3.8:27b et 27b-mlx, muse-glimmer GGUF. Gardés, rôle
  documenté : qwen3.6-nvfp4 (retest prochaine release Ollama), nemotron
  (candidat worker delegate), muse-glimmer:30b-nvfp4-dflash (piste caméra).

## 2026-08-15 — KV cache 32 K, historique borné, dépendances épinglées

- **KV cache : 262 144 → 32 768 tokens** via un tag dérivé
  `qwen3.6:35b-a3b-q4_K_M-ctx32k` (Modelfile `PARAMETER num_ctx 32768`,
  mêmes blobs, zéro disque en plus). Mesuré (`tools/bench_longctx.py`,
  protocole avec tour long ~10 k chars) : TTFT identique au tag de base
  (médian 0,18 s court, 0,18–0,19 s long, prefill froid ~1,3 s dans les
  deux cas), résident **28 → 23 Go (−5 Go)**. Le paramètre du Modelfile
  gagne sur le défaut de l'app Ollama — vérifié via `ollama ps`
  (CONTEXT 32768). Pourquoi un tag dérivé et pas une option par requête :
  l'endpoint OpenAI-compat ne transmet pas `num_ctx`, et un num_ctx
  divergent entre requêtes ferait recharger le modèle.
- **Piège de bench découvert au passage** : sans `reasoning_effort:"none"`
  le TTFT « mesuré » est de 7–17 s (le modèle pense en silence). Tout bench
  doit envoyer les mêmes réglages que bot.py (temp 0,2, reasoning none) —
  `tools/bench_longctx.py` le fait maintenant et remplace le protocole de
  `bench_ttft.py` pour les comparaisons futures.
- **Historique de session borné** (`HistoryTrimmer` dans bot.py) : le
  contexte envoyé au LLM est coupé à système + `MERLIN_MAX_HISTORY_MSGS`
  (défaut 40 ≈ 20 tours). La coupe tombe toujours sur un message user pour
  ne jamais orphaner un résultat d'outil (400 API sinon). La conversation
  complète reste dans transcripts.db — on ne borne que ce que le modèle
  relit à chaque tour. 40 messages ≪ 32 K tokens : les deux plafonds sont
  cohérents.
- **requirements.txt épinglé** aux versions du venv (pipecat-ai 1.3.0 en
  tête) : bot.py monkeypatche des internals Pipecat, un `pip install -U`
  aveugle pouvait casser la voix en silence. Procédure de montée de version
  dans le commentaire du fichier.

## 2026-08-16 — Canal texte : iMessage sortant uniquement (WhatsApp écarté)

Besoin : être prévenu hors de portée du micro (cycle de vie de l'atelier de
skills), et à terme approuver à distance.

- **iMessage retenu pour le sortant** (`notify.py`) : envoi 100 % local via
  AppleScript (`osascript`, texte passé en argv du handler `on run` — aucun
  échappement), la famille l'utilise déjà, zéro dépendance. Best-effort par
  contrat : retourne "sent"/"disabled"/"failed", ne lève jamais, timeout 15 s
  — une notification ne doit jamais casser un build de l'atelier.
- **Destinataire dans `data/notify.json`** (git-ignoré : le numéro ne part
  jamais dans un commit), override env `MERLIN_NOTIFY_IMESSAGE`. Fichier
  plutôt qu'env seul : l'atelier tourne aussi via cron/spawn où l'env du
  shell n'existe pas. Non configuré = canal coupé, silencieux.
- **Piège macOS** : le premier envoi déclenche la demande d'autorisation
  Automation (contrôler « Messages ») pour le binaire APPELANT — à accorder
  une fois par contexte (Terminal, process du bot). Test :
  `venv/bin/python notify.py "coucou"`.
- **Contexte du bot validé (16/08)** : bot.py tourne orphelin sous launchd
  (ppid 1, binaire Python.framework) — identité TCC distincte du terminal.
  Test dans un contexte identique (python du venv orphelin launchd →
  `notify.py`) : « sent ». La chaîne réelle bot → workshop → notify →
  osascript a la même ascendance ; les notifications de l'atelier passeront
  depuis le bot tel qu'il est lancé aujourd'hui.
- **Piège écran verrouillé (vécu 16/08)** : écran verrouillé, la boîte de
  consentement Automation ne peut pas s'afficher et l'Apple Event pend
  jusqu'au timeout (-1712) — même un simple `get accounts` pend. Symptôme :
  `notify.py` renvoie "failed" alors que tout est bien configuré. Diagnostic :
  `ioreg -n Root -d1 -a | grep CGSSessionScreenIsLocked` (présent = verrouillé).
  Une fois l'autorisation accordée (validé 16/08, envoi OK), les envois
  suivants passent ; seule la PREMIÈRE demande d'un nouveau binaire exige un
  écran déverrouillé. Le timeout 15 s de notify.py borne le coût du cas
  verrouillé-sans-autorisation.
- **WhatsApp écarté** : l'API officielle (Cloud API) exige un compte Meta
  Business, un numéro dédié et un webhook public — contraire au local-first ;
  les ponts non officiels (Baileys, whatsapp-web.js) violent les ToS et font
  bannir le compte. Ne pas re-explorer sans changement chez Meta.
- **Entrant (approbation par réponse) différé, volontairement** : lire
  `~/Library/Messages/chat.db` demande Full Disk Access et le schéma bouge
  avec macOS ; surtout, approuver = promouvoir du code généré par IA en
  plugin vif — cohérent avec « préférer ne pas agir », l'approbation reste
  vocale/CLI tant que l'usage réel ne prouve pas le besoin. Si on le fait :
  vérifier le handle expéditeur ET exiger le slug dans la réponse (jamais un
  « oui » nu). Alternative robuste documentée : bot Telegram (long-polling,
  pas de port ouvert, boutons inline, allowlist chat-id) — au prix d'un
  serveur tiers.

## 2026-08-16 — Atelier : fallback codex réparé (dérive de CLI)

- **Casse constatée en production** (build « jouer de la musique dans le
  salon ») : `codex exec --full-auto` n'existe plus — codex-cli 0.147.0 a
  supprimé le flag sur `exec`. Le fallback échouait donc instantanément après
  chaque échec d'agy. Correction dans `run_worker` :
  `codex exec --sandbox workspace-write <prompt>` (exec est non interactif ;
  workspace-write suffit pour écrire les candidats et lancer le smoke test).
  Vérifié en vrai : `run_worker('codex', …)` → rc=0 en ~4 s, dépôt intact.
- **agy hors de cause** : modèle `gemini-3.1-pro-high` toujours listé, flags
  inchangés (1.1.13), réponse en 8 s sur prompt trivial. Le timeout de 900 s
  du 16/08 était transitoire (session de build bloquée côté serveur,
  probablement) — pas de correctif, le timeout + fallback est exactement le
  mécanisme prévu pour ce cas.
- **Leçon** : les CLI des workers dérivent silencieusement (mise à jour
  homebrew/npm) et l'atelier ne s'en aperçoit qu'au prochain build. Si ça se
  reproduit, envisager un smoke test des workers (prompt « OK ») en tête de
  `process_one`, ou après chaque upgrade des CLI.

## 2026-08-17 — Dashboard : vanilla sans build (Voice UI Kit écarté), token partagé

- **Contexte** : item roadmap « Dashboard web (Pipecat Voice UI Kit) » +
  prérequis auth de `/api/offer`. Fred a tranché : **vanilla maintenant**,
  une app plus riche vivra **à côté** à mi-terme.
- **Client : vanilla JS sans build, pas le Voice UI Kit React.** Raisons :
  (1) les quatre panneaux voulus (gate, transcript attribué, atelier, cartes
  d'outils) sont des composants custom dans les deux cas — le kit n'apporte
  que le bouton connect et un visualiseur ; (2) le protocole RTVI côté client
  est un `switch` sur du JSON du data channel (vérifié contre
  `pipecat/processors/frameworks/rtvi/` 1.3.0, protocole 1.4.0) ; (3) zéro
  toolchain Node dans un dépôt jusque-là sans build ; (4) pas de dérive de
  version entre `@pipecat-ai/client-js` et pipecat épinglé 1.3.0. **Le choix
  ne ferme rien** : le contrat (token Bearer, REST `/api/workshop*`, messages
  RTVI) est le même pour une future app avec build.
- **Auth : token partagé** (`MERLIN_TOKEN` ou `data/auth-token` auto-généré
  chmod 600, comparaison `secrets.compare_digest`), en header
  `Authorization: Bearer` uniquement — jamais en query string (les URLs
  finissent dans les logs d'accès). La page statique reste publique ; le
  token ne protège que les actions (offer = GPU, approve = activation de code
  généré). Tailscale écarté comme *mécanisme* d'auth (dépendance infra,
  exclut un invité du LAN) mais compatible par-dessus.
- **Câblage serveur** : RTVI activé sur `PipelineWorker` (l'ex
  `enable_rtvi=False` datait d'avant le besoin) ; `function_call_report_level`
  FULL (args + résultats des outils sur le data channel : LAN + token, outils
  domestiques). Décisions du gate : `VoiceGate` pousse un
  `RTVIServerMessageFrame` (`{event: "gate-decision", accepted, reason,
  speaker, text, ts}`) que l'observer RTVI convertit en `server-message` —
  aucun couplage bot.py↔gate, no-op si RTVI éteint. Le keep-alive `ping` brut
  du client est court-circuité par la couche connexion SmallWebRTC avant le
  parseur RTVI (vérifié dans le source) — il reste tel quel.
- **Approbation dashboard** : `POST /api/workshop/approve` exige la même
  preuve que la voix/CLI (entrée `skill-ready.jsonl`, donc gates AST + smoke
  test passés) + slug validé `^[a-z0-9_]{1,40}$`. Confirmation à deux taps
  côté client (pas de `confirm()` bloquant).
- **Vérifié bout-en-bout** avec `tools/probe_rtvi.py` (client WebRTC headless
  aiortc, voix de synthèse macOS `say`) : handshake client-ready→bot-ready,
  transcription Whisper parfaite, gate-decision reçu sur le data channel
  (« voix inconnue (sim=0.14) » — rejet attendu d'une voix non inscrite).
  Tests offline : `tools/test_dashboard_api.py`. Non vérifié : rendu visuel
  (écran verrouillé pendant la session) — à contrôler à la première ouverture.
- **Setup d'un appareil** : ouvrir `https://<host>:7860/#token=<token>` une
  fois (stocké en localStorage, retiré de l'URL). Token : `cat data/auth-token`.

## 2026-08-17 — Chat texte : send-text RTVI, gate contourné à dessein, log LLM-side

- **Mécanisme** : le RTVIProcessor de pipecat 1.3.0 gère nativement
  `send-text` → `LLMMessagesAppendFrame` (+ `LLMConfigureOutputFrame` si
  `audio_response=false`) — aucun code serveur pour l'envoi. Même session,
  même contexte, mêmes outils que la voix ; nécessite une connexion WebRTC
  active (pas de canal REST séparé : ça forkerait l'état de conversation).
- **Le clavier contourne le VoiceGate, et c'est voulu** : le gate protège le
  micro (canal ouvert à quiconque est dans la pièce) ; la saisie exige le
  token Bearer, l'expéditeur est donc déjà authentifié. Pas de mot d'éveil,
  pas de vérif locuteur sur le texte tapé.
- **Réponse silencieuse** : `audio_response=false` marque les TextFrames du
  LLM `skip_tts` — le TTS les saute mais elles continuent de descendre le
  pipeline (vérifié dans llm_service/tts_service). Conséquences exploitées :
  (1) le client rend les réponses depuis `bot-llm-text` (streaming token par
  token, remplace `bot-tts-text` — un seul chemin de rendu voix/texte) ;
  (2) le log des réponses a été déplacé du wrapper `run_tts` vers
  `AssistantResponseLogger` (processor après le llm) — sinon les réponses
  silencieuses n'étaient jamais journalisées. `run_tts` ne garde que
  recent_tts_words/last_bot (écho + détection de question, purement oraux).
- **Journal** : tours tapés préfixés `[clavier]` dans transcripts.db (à
  filtrer du jeu de tuning STT, comme `[filtré: …]`). Le texte assistant
  loggé est désormais la réponse LLM complète (une ligne par run LLM), plus
  du par-phrase synthétisé ; un barge-in peut toujours couper la lecture
  après le log.
- **Vérifié** : `tools/probe_rtvi.py "Réponds juste avec le mot bonjour."` →
  réponse `bonjour` en bot-llm-text, aucun bot-tts-started, et les deux
  lignes attendues dans transcripts.db (`[clavier] …` + `assistant`).

### Correctif du 17/08 : send-text abandonné (bug upstream sur les tours à outils)

- **Bug constaté par Fred en prod** : 1er message tapé silencieux OK, le 2e
  parlait — le 2e était une question météo, donc un tour à outil = **deux
  runs LLM**. Le `send-text` natif pousse configure(skip)→append→configure
  (restore) : le restore tombe entre le run 1 (tool call) et le run 2 (la
  vraie réponse), qui repart donc en TTS. Défaut de conception upstream —
  ajouté à la liste des issues Pipecat à déposer (ROADMAP Ops).
- **Correctif** : le client envoie un `client-message` custom
  `{t:"chat", d:{text, speak}}` ; bot.py (handler `on_client_message`) pousse
  interrupt + `LLMConfigureOutputFrame(skip_tts)` **sans restore** (mode
  collant) + append. Le retour au parlé est fait par le prochain tour vocal
  accepté : VoiceGate pousse `LLMConfigureOutputFrame(skip_tts=False)`
  (une question orale a toujours une réponse orale).
- **Deuxième fuite trouvée dans la foulée** : les plugins poussent des
  `TTSSpeakFrame` (« Je regarde ça. ») qui contournent le marquage skip_tts
  du LLM — le filler parlait pendant un tour silencieux.
  `SilentTurnTTSFilter` (entre llm et tts) les coupe **seulement pendant un
  tour silencieux actif** (skip_tts lu sur le LLMFullResponseStartFrame
  marqué + compteur d'appels d'outils en vol) : l'alarme différée du
  minuteur (« C'est l'heure ! »), qui arrive à vide, passe toujours. Limite
  connue : une alarme tombant exactement pendant un tour silencieux actif
  serait coupée (rarissime, accepté).
- **Re-vérifié sur le scénario exact du bug** (météo tapée en silencieux) :
  zéro événement TTS, filler coupé (log `SilentTurnTTSFilter`), réponse
  complète en bot-llm-text ; et en mode parlé la même question déclenche
  bien le TTS.

## Incidents (à ne pas reproduire)

- **13/08 : profil vocal réel détruit par un test.** La migration du profil
  historique utilisait un chemin module-level ; un test passant un répertoire
  temporaire l'a déclenchée et le fichier réel a été déplacé puis supprimé
  avec le tmpdir. Garde ajoutée (`root == VOICES_DIR`) ; règle : les tests ne
  touchent jamais les chemins réels, et re-vérifier ce point à chaque nouveau
  chemin module-level.
- **13/08 : redémarrage fantôme.** `pkill -f "python bot.py"` ne matche pas le
  binaire macOS `Python` (majuscule) ; l'ancien process a continué à servir le
  vieux code pendant qu'on déboguait le neuf. Toujours tuer par PID du port.

## 2026-08-18 — Attribution du locuteur + launchd

- **Attribution : NULL plutôt qu'un nom deviné.** Colonne `speaker` dans
  `turns` (migration `ALTER TABLE` au démarrage, testée sur l'ancien schéma).
  `GateCore.last_speaker` est posé uniquement sur les chemins où l'identité
  est établie : les fail-open (« vérification indisponible », gate désactivé)
  passent le tour mais restent NULL — même philosophie que le gate (ne pas
  agir > agir à tort). Les tours courts non vérifiés sont crédités à
  l'activateur (cohérent avec la décision d'acceptation, qui les traite déjà
  comme lui). Les tours rejetés et `[clavier]` restent NULL.
- **Bug latent corrigé au passage** : le champ `speaker` du message RTVI
  `gate-decision` utilisait `core.activator` — faux en mode famille (un
  membre non-activateur accepté était affiché comme l'activateur). Il
  utilise désormais `last_speaker` (la personne qui a réellement parlé).
- **launchd : le bot passe sous `com.merlin.bot` (KeepAlive).** Motivation :
  survie au reboot (reliquat revue 13/08) + relance auto sur crash. Le
  redémarrage documenté devient `launchctl kickstart -k` ; un `kill` simple
  relance aussi (KeepAlive) — le piège `pkill` du 13/08 devient sans objet
  mais la règle reste. Pour un run manuel (dev), bootout d'abord, sinon le
  port est tenu. `ollama serve` : pas d'agent à nous, Ollama.app gère son
  propre démarrage (vérifié : tourne sans login item classique).
- **`com.merlin.warmup` supprimé** : il curl-ait le health d'un router :8101
  mort ; son rôle (garder le modèle chaud) est couvert depuis le 15/08 par
  `_preload_llm()` (`keep_alive:-1`). **Monitor réécrit** (`ops/check-ai-stack.sh`,
  source de vérité dans le repo, `~/scripts/check-ai-stack.sh` = wrapper) :
  Ollama, modèle épinglé via `/api/ps` (un ps vide = retour du cold start
  6–7 s), bot :7860 ; iMessage via `notify.py` sur transition ok↔fail
  seulement (état dans `/tmp/merlin-monitor.state`) — avant, il loggait
  « 5 failed » toutes les 5 min depuis des semaines (403 Ko de log) sans que
  personne ne le voie : un monitor qui ne notifie pas ne surveille rien.
- **`com.wyoming.whisper`/`com.wyoming.piper` laissés en place** (chargés,
  ports :10300/:10200 actifs). Wyoming est le protocole voix de Home
  Assistant et le HA Yellow pourrait les consommer — à confirmer avant
  suppression (piège classique : « périmé pour Merlin » ≠ « périmé »).
- **Piège bash dans le monitor** : `((PASS++))` renvoie le statut 1 quand
  PASS vaut 0, ce qui déclenchait le `|| fail` (« ok » ET « FAIL » sur la
  même ligne de check). Remplacé par `PASS=$((PASS+1))`.

## 2026-08-18 — Phrase d'arrêt et mode privé

- **Cas d'usage** : quelqu'un entre dans la pièce pendant qu'on parle à
  Merlin — il faut pouvoir couper la reconnaissance d'une phrase. Trois
  expositions traitées : la fenêtre d'attention ouverte (12–30 s), la réponse
  TTS en cours, et surtout la journalisation : Whisper transcrit tout et tout
  finit dans transcripts.db (même `[filtré:]`) — la conversation de l'invité
  y serait. Le mode privé n'enregistre RIEN avec contenu.
- **Asymétrie inversée, décision centrale.** Le gate préfère ne pas agir ;
  pour un STOP c'est l'inverse : un faux stop coûte un ré-éveil, un stop raté
  est l'échec de vie privée. Donc : n'importe quelle voix peut arrêter, sans
  vérification, même en phrase courte. Et symétriquement, **lever** le mode
  privé est plus strict qu'un éveil normal : phrase complète vérifiée par une
  voix inscrite, pas de barre courte (« Merlin ? » à sim 0,77 par une voix de
  synthèse a été observé — la leniency courte ne doit jamais lever le privé),
  échec d'embedding = fermé (seul endroit où le fail-open s'inverse),
  inscription suspendue (ne jamais absorber un invité).
- **Écarté : outil LLM** (« Merlin arrête d'écouter ») — lent, probabiliste,
  et dépend de la chaîne qu'on veut justement court-circuiter. Un stop est un
  réflexe, pas un raisonnement.
- **Mots choisis** : « chut » et « stop », appariés au mot d'éveil dans le
  même énoncé (les deux ordres). « pause » écarté (collision future : «
  Merlin, mets le minuteur en pause »). « stop-kill » (idée Fred) marche via
  le token « stop » après normalisation. Un « chut » seul ne coupe rien
  (chuchoter à un enfant en plein échange ne doit pas tuer la session).
- **Double canal, comme l'éveil.** Brut (zipformer) : coupe le TTS en ~20 ms
  via `broadcast_interruption()` depuis le WakeWordListener et pose le hold
  par callback (`GateCore.enter_hold`), sans attendre VAD+Whisper.
  Transcription : filet de sécurité dans `GateCore.evaluate`, avant toute
  autre logique.
- **Calibration mesurée (synthèse Kokoro + say)** : le zipformer décode
  l'interjection « chut » en CHU / CHUS / SUT / CHUTE / **SHUT** (la
  fricative sort en S ou SH, la consonne finale est instable) — regex de
  variantes par mot exact, « su » nu exclu (« j'ai su… »), « parachute » et
  « stoppe » restent des mots différents. **Whisper transcrit « Chut ! » en
  « chute. »** → « chute » ajouté au défaut `MERLIN_STOP_WORDS` (faux stop
  possible sur « Merlin … chute … », accepté : coût = un ré-éveil).
- **Bug trouvé au passage dans le moteur d'éveil** : le reset du stream au
  moment du fire de l'éveil avalait le mot suivant en cours de décodage
  (« Merlin, stop. » resetté à [MERLIN S] ne laissait que [TOP]). Le fire ne
  reset plus ; un flag par segment empêche le re-fire, le reset se fait à
  l'endpoint. Le gate lisant l'éveil avec 3 s de marge, rien ne dépendait du
  fire-et-reset.
- **Piège de test** : l'endpoint du zipformer peut demander >2 s de silence
  de fin — les tests streaming paddent à 3,5 s (en prod l'audio ne s'arrête
  jamais).

## 2026-08-18 — Compléments du stop : restriction, HTTP, bouton

- **`MERLIN_STOP_ACTIVATOR_ONLY` (défaut 0)** : demande de Fred (« seul
  celui qui a lancé l'échange peut l'arrêter »). Implémenté en option, pas en
  défaut, car il affaiblit la garantie de vie privée : les phrases d'arrêt
  sont courtes (~1 s) et leur embedding est instable (un vrai « Merlin ? » a
  déjà scoré 0,22 contre son propre profil) — toute vérification stricte
  raterait de vrais stops. Compromis retenu : barre indulgente
  (SHORT_WAKE_SIM 0,35, profil de l'activateur OU ancre de l'échange), échec
  d'embedding = stop quand même, pas d'activateur lié = tout le monde peut
  arrêter (rien à détourner). Le canal audio brut n'a aucune identité de
  voix : dans ce mode il ne fait que couper le TTS (même autorité que le
  barge-in, ouvert à tous) et laisse la décision de hold à la passe
  transcription. Un stop refusé répond « stop refusé (pas l'activateur) »
  sans traiter l'énoncé (surtout ne pas re-binder l'échange sur le refusé).
- **`POST /api/stop`** (Bearer) : le token = autorité du foyer, comme le
  clavier du dashboard qui contourne le VoiceGate — la restriction
  activator-only ne s'y applique pas. Coupe et met en hold TOUTES les
  sessions actives (registre `dashboard_api.register_session`, rempli par
  bot.py à chaque pipeline, nettoyé en finally). Pousse un événement
  `gate-decision` (même forme que ceux du VoiceGate) sur le data channel —
  le dashboard l'affiche sans logique cliente nouvelle. Endpoint dans
  dashboard_api.py (pas bot.py) pour rester testable hors-ligne avec des
  fakes duck-typés (`tools/test_dashboard_api.py`).
- **Bouton « 🤫 Chut »** (dashboard) : HTTP volontairement, pas RTVI —
  marche depuis un navigateur qui n'a PAS la session WebRTC (cas réel : le
  téléphone porte la session, on coupe depuis le laptop), et arrête tout.
  Toujours visible, contrairement à « Déconnecter ».
- Vérifié en vrai : probe connectée + `curl /api/stop` → `{"stopped":1}`,
  hold posé, la probe reçoit le `gate-decision` « (stop manuel) ».
- **Scope par personne (demande Fred, même jour)** : `POST /api/stop` exige
  `{"speaker": "<nom>"}` — nom validé contre les profils inscrits
  (`data/voices/*.npz`, glob direct pour rester importable sans pipecat dans
  les tests hors-ligne ; 404 sinon, 400 sans nom) et n'arrête que les
  sessions dont cette personne est l'**activateur** (les échanges des autres
  membres continuent). Le bouton du dashboard envoie l'activateur courant
  appris des événements `gate-decision` acceptés ; sans échange connu il ne
  fait rien (« Aucun échange en cours à arrêter »). Un activateur périmé
  côté client reste inoffensif : au pire un hold en trop, un ré-éveil.
