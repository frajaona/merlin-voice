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

## 2026-08-18 — Sonos : architecture arrêtée (HA + lecture native), plan en 4 phases

Étude comparative (session Claude du 18/08) ; le plan détaillé vit dans
`docs/SONOS.md`. Contexte décisif : un HA Yellow tourne déjà au foyer ;
service principal = **Apple Music**, Spotify surtout pour les enfants,
bibliothèque perso sur NAS ; Music.app connectée sur le Mac de Merlin.

- **Plan de contrôle = Home Assistant (Yellow), API REST + token.** Battait
  SoCo-direct uniquement parce que HA existe déjà (sinon SoCo gagnait :
  zéro service en plus). Transport/volume/groupes = services `media_player.*`
  standard ; l'intégration Sonos de HA est elle-même bâtie sur SoCo.
- **Lecture toujours native Sonos** (comptes liés dans l'app Sonos) : les
  enceintes streament seules, rien ne dépend du Mac. Merlin résout la demande
  en lien de partage (mécanisme ShareLink : Spotify/Apple Music/Tidal/Deezer)
  et le donne à l'enceinte.
- **Apple-Music-first a inversé un choix** : Music Assistant était d'abord
  recommandé pour la recherche multi-services, mais son provider Apple Music
  est du reverse-engineering communautaire (auth cassable) — inacceptable
  pour le service PRINCIPAL. Remplacé par : iTunes Search API (catalogue,
  gratuite sans auth) + **bibliothèque Music.app via AppleScript** (playlists
  et albums perso, iCloud Music Library = copie locale complète, zéro
  credential — rend MusicKit et son abonnement développeur inutiles).
- **Music.app → AirPlay 2 = chemin secondaire assumé** (playlists perso sans
  lien public ; AppleScript sait choisir les appareils AirPlay). Implique un
  routage par source pour pause/suivant (phase 3 du plan).
- **Écartés** : Music Assistant (différé — se rouvre si limite 65 k pistes
  NAS, besoin de transfer_queue, ou métadonnées riches), node-sonos-http-api
  (démon Node redondant), API cloud Sonos (anti local-first), MusicKit
  (redondant avec Music.app).
- **Plafond documenté** : Apple Music n'a AUCUN transfert de session
  vers/depuis des enceintes tierces (handoff = approximations : AirPlay
  manuel, lien iMessage `music.apple.com`). Spotify Connect, lui, fait le
  vrai transfert iPhone↔Sonos (`PUT /me/player`) — phase 4.
- **À vérifier en phase 0** (peut déplacer une frontière du plan) :
  `media_player.play_media` de HA relaie-t-il les liens de partage sur
  Sonos ? Sinon fallback confiné SoCo ShareLink dans le plugin.
- Contraintes projet : plugins écrits main (réseau/subprocess = bannis du
  scan AST de l'atelier) ; principe du gate appliqué à la musique (ambiguïté
  → demander, pas jouer au hasard) ; politique voix enfants à trancher en
  phase 4.

## 2026-08-18 — Sonos phase 1 : choix d'implémentation de `sonos_controle`

- **Découverte des entités par template HA** (`integration_entities('sonos')`
  via POST `/api/template`, cache 10 min) plutôt qu'un filtre heuristique sur
  les attributs : c'est le seul moyen REST propre d'isoler les media_player
  Sonos des autres (TV, casts). Fallback assumé si le template échoue : tous
  les `media_player.*` (documenté dans le test).
- **Pas d'action « stop »** dans le schéma, seulement « pause » : « Merlin
  stop » est la phrase d'arrêt/mode privé (2026-08-18) — un outil « stop »
  entrerait en collision avec elle dans les transcriptions. À garder en tête
  pour `sonos_musique` (phase 2).
- **Ambiguïté = refus d'agir** (principe du gate appliqué aux outils) :
  pièce inconnue/ambiguë → erreur listant les enceintes ; aucune pièce
  précisée → on n'agit que si UNE seule joue (ou une seule en pause pour
  « lecture », ou `MERLIN_SONOS_DEFAULT_ROOM`) ; plusieurs jouent → on
  demande. Aucun appel de service ne part dans ces cas (testé).
- **Volume relatif calculé** (état lu puis `volume_set` absolu, pas
  `volume_up/down`) : pas de course sur les steps de 5 % de HA, et « +10 »
  ou « moins » deviennent déterministes. Échelle parlée 0–100, HA en 0–1.
- **Pas de filler parlé** dans ce plugin : deux appels REST LAN (~100 ms),
  sous la barre de ~1 s du contrat plugins.
- **`grouper` : `piece` = la pièce ajoutée, `valeur` = celle dont la musique
  continue (master du `media_player.join`)** ; master implicite = la seule
  pièce en lecture. Correspond au français « groupe la cuisine avec le
  salon ».
- **Monitor** : le check HA de `check-ai-stack.sh` n'est actif que si
  `data/ha-token` existe — HA ne devient une dépendance surveillée qu'au
  moment où le foyer s'en sert vraiment.
- Vérifié le 18/08 : HA joignable (`http://homeassistant.local:8123`,
  192.168.240.143, 401 propre sans token) ; Music.app scriptable depuis le
  terminal (TCC ok, version 1.6.6) — l'autorisation pour le contexte
  launchd du bot reste à valider en phase 3.

## 2026-08-19 — Sonos phase 0 close : verdicts mesurés

- **Verdict liens de partage (décisif phase 2) : HA `play_media` relaie
  nativement les liens Apple Music ET Spotify vers Sonos.** Testé en vrai
  sur la Roam (volume 5 %, remis à 45 %) : `media_content_type: "music"` +
  URL `music.apple.com/fr/album/...` → lecture (Daft Punk, Discovery) ;
  idem `open.spotify.com/album/...`. **Pas de fallback SoCo — un seul
  chemin de lecture, comme espéré dans `docs/SONOS.md`.**
- **Inventaire (7 enceintes)** : Séjour, Cuisine, Bureau, Chambre, Chambre
  Loulou, Chambre Gaby, Sonos Roam. **Piège : friendly name ≠ entity_id**
  (`media_player.bureau` s'appelle « Chambre », `media_player.unnamed_room`
  s'appelle « Bureau ») — toujours matcher les friendly names, jamais les
  entity_ids. Chambre Loulou joue en continu à 6 % (bruit de sommeil
  probable) : à garder en tête pour la logique « seule pièce en lecture »
  et la future politique enfants.
- **mDNS `.local` inutilisable depuis le process launchd du bot** : getaddrinfo
  pend ~35 s puis Errno 8, alors que curl/shell résolvent — d'où
  `data/ha-url` (IP épinglée, prioritaire sur le défaut
  `homeassistant.local`). Penser à une réservation DHCP pour le Yellow
  (192.168.240.143).
- **Permission macOS « Réseau local » requise pour « Python »** : premier
  accès LAN du bot → prompt TCC (vu à l'écran via Peekaboo), signature de
  refus = `Errno 65 No route to host` vers une IP LAN pendant que curl
  passe (curl = identité du terminal, déjà autorisée). Le prompt a expiré
  sans réponse et n'est plus cliquable (UserNotificationCenter sans
  fenêtre) ; l'automatisation Réglages Système a échoué (écran
  verrouillé) → toggle manuel : Réglages Système → Confidentialité et
  sécurité → Réseau local → Python ON. S'applique à TOUT python du Mac
  (le venv du shell est logé à la même enseigne, seul curl passait).
- **Wyoming : conserver `com.wyoming.*`** — `integration_entities('wyoming')`
  sur le Yellow liste `stt.faster_whisper`, `stt.mlx_whisper`, `tts.piper`,
  `tts.piper_2` : le Yellow consomme bien les services Wyoming de ce Mac.
- **Music.app opérationnelle pour la phase 2** : après ouverture + session
  (Fred, 19/08), 248 playlists lisibles en AppleScript, enceintes Sonos
  visibles comme appareils AirPlay. Les timeouts AppleEvent -1712 du 18/08
  = app jamais ouverte, pas un problème TCC.

## 2026-08-19 — Permission « Réseau local » : résolution, et phase 1 validée

- **Le panneau Réseau local contient PLUSIEURS entrées Python** (constaté
  dans `/Library/Preferences/com.apple.networkextension.plist` :
  `/usr/bin/python3`, un cpython uv 3.11, et `org.python.python`). Celle du
  bot (et du venv) est **« Python » = `org.python.python`** (le framework
  Homebrew python@3.12). Diagnostic différentiel : `/usr/bin/python3`
  passait, `venv/bin/python` recevait Errno 65 → mauvaise entrée activée
  au premier essai.
- **Le grant ne s'applique qu'aux processus démarrés après** : après
  activation du bon toggle, il a fallu `launchctl kickstart -k` du bot
  (un bot déjà lancé garde Errno 65).
- Aide-mémoire : `Errno 65 No route to host` vers une IP LAN pendant que
  curl passe = permission Réseau local, pas un problème réseau.
- **Phase 1 validée en vrai** (probe RTVI tapée, même LLM/outils que la
  voix) : « Remets la musique dans la cuisine » → lecture réelle vérifiée
  côté HA ; « volume à 15 » → 0.15 vérifié ; « qu'est-ce qui joue ? » →
  titre réel restitué ; « pause » → paused vérifié. Volume Cuisine remis
  à 10 % après les tests. Une réponse chat vide observée sur un tour à
  outil (l'action a bien eu lieu) — probablement le timing de la sonde,
  à surveiller en usage vocal réel.

## 2026-08-19 — Incident gate du petit-déjeuner (données de calibration)

Contexte : tests Sonos de Fred, cuisine, musique en cours, famille présente.
Deux ratés rapportés par Fred, tous deux conformes au design actuel — mais
le coût réel est maintenant mesuré (tout est dans transcripts.db) :

- **Faux rejet** : phrase d'éveil claire de Fred à **sim=0.45** (loin
  champ + musique dans la pièce ; sa plage propre : 0.72–0.89 ; répétée de
  plus près : 0.90). 0.45 est DANS la plage mesurée de sa femme
  (0.08–0.54) → ne PAS baisser le seuil 0.60 ; la réponse est un top-up de
  profil en conditions cuisine (`voice_profile.py enroll fred`).
- **Fausse acceptation** : « Merci. » de sa femme accepté 3× via la
  **leniency tours courts** (`fred (court, non vérifié)`) pendant la
  fenêtre question de 30 s — ouverte parce que le LLM finit ses réponses
  par « Tu veux que je change quelque chose ? », et ré-ouverte à chaque
  « De rien ! ». Ses phrases longues étaient correctement rejetées
  (sim 0.06–0.16). L'échange est resté ouvert > 1 min (épisode « lapin »).
- **Pistes retenues (non implémentées)** : (a) mots de clôture
  (« merci », « d'accord ») → fermer l'échange au lieu de répondre, en
  gardant oui/non ; (b) fenêtre question moins généreuse (prompt LLM sans
  question de politesse, ou MERLIN_QUESTION_SECS 30→15, ou fenêtre armée
  seulement sur vraie clarification d'outil) ; (c) inscrire la femme et
  les enfants (ROADMAP item 1) — préalable à toute leniency plus fine.

## 2026-08-19 — Clôture polie et fenêtre question (suite de l'incident)

Fred a validé les deux pistes (a) et (b) de l'incident du matin :

- **Clôture polie** (`is_polite_closer`, voice_guard.py) : pendant un
  échange, un énoncé qui n'est QUE remerciement/adieu (≤ 6 mots, au moins
  un cœur `merci/revoir/bientot/adieu`, le reste dans une liste fermée de
  mots d'accompagnement) **ferme l'échange** (`_close_exchange`) et n'est
  pas répondu — motif `[filtré: clôture polie (échange fermé)]`. Choix
  clés : (1) les cœurs excluent « ok/d'accord/oui/non » — ce sont des
  réponses légitimes aux questions du bot, ils restent couverts par la
  leniency courte ; (2) la vérification passe AVANT la leniency courte
  (c'est elle qui créditait les « Merci. » d'un tiers à l'activateur) ;
  (3) **seul l'activateur peut clore** (demande Fred, même jour — la
  première version acceptait toute voix) : barre indulgente
  `_is_activator_lenient` (SHORT_WAKE_SIM contre profil OU ancre vive,
  comme le stop activator-only), mais contrairement au stop une voix
  invérifiable (embedding manquant ou sous la barre) NE clôt PAS — clore
  est une action, le gate préfère ne pas agir. Une clôture non-activateur
  est **ignorée** : ni « De rien ! », ni fermeture, la fenêtre expire
  d'elle-même (`[filtré: clôture polie ignorée …]`). Le chemin fail-open
  (embedding indisponible) ignore aussi les clôtures pures au lieu de les
  accepter. Coût assumé : un « Merci. » trop court de l'activateur peut
  être ignoré (les embeddings 1 mot peuvent scorer bas même pour leur
  locuteur) — l'échange ne ferme pas mais ne répond rien, la fenêtre
  meurt en 12–15 s. « Merci, merci, Daniel. » ne clôt PAS (mot hors
  liste) — il repasse par la leniency, comme dans l'incident : la clôture
  ne traite que les tours purs.
- **Fenêtre question 30 → 15 s** (`MERLIN_QUESTION_SECS`) + prompt système :
  interdiction de terminer par une question de politesse (« Tu veux autre
  chose ? ») — c'était le mécanisme qui armait la fenêtre longue en continu
  pendant que la famille parlait. La fenêtre longue ne sert plus qu'aux
  vraies clarifications (il manque une info pour agir).
- Tests : `test_polite_closer` (classification + fermeture réelle +
  leniency oui/non préservée + clôture par l'activateur vérifié).
  Suites voice_guard et wake_word vertes, bot redémarré.

## 2026-08-19 — Sonos phase 2 : choix d'implémentation de `sonos_musique`

- **Périmètre ajusté vs le plan** : (1) **NAS reporté en 2b** — l'API REST
  de HA ne sait ni parcourir ni chercher la bibliothèque Sonos (media
  browse = websocket uniquement) ; le faire proprement passera par le WS
  de HA ou SoCo, pas par un bricolage. (2) **Résolveur Music.app déplacé
  en phase 3** avec sa lecture AirPlay : les playlists perso Apple Music
  n'ont pas de lien de partage public (injouables avant la phase 3), et
  reconnaître sans savoir jouer coûterait un second consentement TCC
  (Automation launchd → Music.app) pour un simple « je ne peux pas ».
- **Playlists = alias uniquement** (`data/sonos-aliases.json`,
  `{"nom parlé": "url"}`, match flou ≥ 0.75) : iTunes Search n'a PAS
  d'entité playlist, et chercher « ma playlist jogging » dans les
  playlists publiques Spotify jouerait n'importe quoi — refus assumé avec
  conseil (alias/favori), sauf Spotify demandé explicitement. Principe du
  gate appliqué au contenu.
- **« Joue <artiste> »** : un artiste n'a pas de lien jouable → on prend
  son album le plus en vue (iTunes `attribute=artistTerm`, 1er résultat ;
  Spotify : 1er album de l'artiste) et **on l'annonce** dans la réponse.
  Raffinement possible plus tard : top-titres en file d'attente
  (`enqueue: add` multiple).
- **Seuil de correspondance 0.60** (difflib sur chaînes normalisées,
  bonus sous-chaîne 0.85) : en dessous, on renvoie les candidats au lieu
  de jouer. Vérifié en vrai : « Discovery Daft Punk », « Aldebert »
  (→ Enfantillages), « libérée délivrée » (accents) résolvent au premier
  coup sur le store FR.
- **Apple Music d'abord, Spotify explicite ou secours** : credentials
  client-credentials requis (app à créer sur developer.spotify.com →
  `data/spotify-app.json` `{"client_id": …, "client_secret": …}` ou env
  `MERLIN_SPOTIFY_ID/SECRET`) ; sans eux le résolveur Spotify se
  désactive proprement (Apple Music et alias continuent de marcher).
- **Plomberie partagée `plugins/_sonos_common.py`** (préfixe `_` = jamais
  chargé comme plugin) : ha_request/états/matching de pièces extraits de
  sonos_controle, importés par les deux plugins (`from plugins import
  _sonos_common`) — une seule implémentation à maintenir.
- Vérifié bout-en-bout (probe RTVI) : lecture réelle de Discovery dans la
  Cuisine, refus de playlist inconnue, pause. Nuance observée : sur le
  refus, le LLM propose l'atelier au lieu de relayer le conseil alias —
  acceptable (le refus est le comportement critique), à surveiller.

## 2026-08-19 — Sonos phase 2b : NAS + favoris via le websocket HA

- **Websocket HA plutôt que SoCo** pour parcourir l'index Sonos : garde le
  plan de contrôle unique (décision du 18/08 : pas deux chemins vers les
  mêmes enceintes). `ws_browse` dans `_sonos_common.py` (aiohttp, déjà une
  dépendance pipecat ; connexion éphémère, `asyncio.run` sûr car appelé
  depuis le thread `to_thread`). La lecture reste `play_media` REST — les
  ids du browse (`A:ALBUMARTIST/...`, `A:ALBUM/...`, `S://...`, `SQ:n`)
  passent tels quels, vérifié en vrai.
- **Mesuré sur le vrai NAS** : 229 artistes, 419 albums, 55 playlists
  iTunes (doublons `iTunes Library.xml`/`iTunes Music Library.xml` —
  dédupliqués par titre normalisé), très loin de la limite Sonos de 65 k.
  **Le listing des pistes plafonne à 1000 éléments** → recherche par titre
  NAS = best effort, chargée seulement sur demande explicite (type titre).
- **Conteneurs jouables** : artistes et albums du NAS ont `can_play=True` —
  « joue Adele depuis le NAS » joue TOUT l'artiste (mieux que le compromis
  « meilleur album » des catalogues). Favoris Sonos = `favorite_item_id`
  (`SQ:n` pour les playlists Sonos) — le « filet de sécurité » du plan est
  livré, et couvre les playlists perso en attendant la phase 3.
- **Ordre playlists** : alias → favoris Sonos → playlists NAS → Spotify
  explicite → refus. Ordre catalogue (album/artiste/titre) : Apple Music →
  Spotify (explicite/configuré) → NAS → favoris. `service=nas` (« depuis
  le NAS », « la bibliothèque ») court-circuite tout sauf les alias.
- **Leçon de rechargement** : les plugins sont rescannés à chaque
  conversation, MAIS `plugins/_sonos_common.py` est un import régulier
  gardé dans `sys.modules` du process bot — **toute modification d'un
  module partagé `_*.py` exige un redémarrage du bot** (constaté en vrai :
  `no attribute 'ws_browse'` après édition sans restart).
- Cache browse 10 min (`_browse_cache`) : la bibliothèque bouge rarement ;
  premier appel ~1–3 s (couvert par le filler « Je lance ça »), suivants
  instantanés. Vérifié bout-en-bout par la voix : artiste NAS, playlist
  favorite ; suites test_sonos_musique (11 groupes) et test_sonos_controle
  vertes.

## 2026-08-19 — Cache bibliothèque NAS : quotidien, sur disque (demande Fred)

- Le cache mémoire 10 min de la 2b devient **un cache disque quotidien**
  (`data/sonos-library.json`, TTL `MERLIN_SONOS_LIBRARY_TTL` = 86400) : la
  bibliothèque ne bouge presque jamais, et le disque survit aux
  redémarrages du bot (le premier « depuis le NAS » du jour est le seul à
  payer le scan ; catégories chargées à la demande).
- **Filet anti-péremption** : une recherche NAS infructueuse avec un cache
  de plus d'UNE heure déclenche un re-scan unique puis un re-essai — un
  album rippé dans la journée reste trouvable sans attendre demain ; les
  requêtes fantaisistes ne re-scannent pas en boucle (rate limit 1 h).
- Les **favoris** gardent le cache mémoire court (10 min) : on étoile
  souvent des nouveautés dans l'app Sonos.
- Testé : persistance à travers un redémarrage simulé (websocket mort,
  résolution depuis le disque), re-scan sur cache périmé, rate limit.
  Vérifié en vrai (« Joue Patty Griffin depuis le NAS », cache écrit :
  229 artistes).

## 2026-08-19 — Re-scan NAS : manuel uniquement (bouton dashboard)

Fred a retiré le filet « re-scan sur échec » du cache quotidien : **aucun
re-scan automatique** — comportement plus prévisible, pas de latence
surprise sur une demande ratée. À la place : **bouton « 🔄 NAS »** dans
l'en-tête du panneau Outils du dashboard → `POST /api/sonos/refresh`
(Bearer, comme le reste) → `refresh_library()` re-scanne les 4 catégories
et réécrit `data/sonos-library.json`, réponse = comptes par catégorie
(affichés dans la barre de statut). Le cache et `refresh_library` ont
déménagé de sonos_musique vers `plugins/_sonos_common.py` pour être
joignables depuis `dashboard_api` sans passer par le chargeur de plugins.
Conséquence assumée : un album rippé aujourd'hui n'est trouvable qu'après
un clic sur le bouton (ou l'expiration du TTL 24 h). Tests :
`test_sonos_musique` (pas de re-scan sur échec, le bouton transforme un
échec en succès) + `test_dashboard_api` (`/api/sonos/refresh` : 401 sans
token, comptes, 502 propre). Vérifié en vrai : endpoint → re-scan complet
(229/419/1000/55).

## 2026-08-19 — Playlists : Apple Music avant Spotify (MusicKit)

Demande Fred : les playlists doivent passer par Apple Music avant Spotify.
Contrainte technique : **l'iTunes Search API n'a pas d'entité playlist**
(vérifié : `entity=playlist` → erreur, `entity=mix` → 0 résultat), et le
token anonyme du web player n'est plus extractable des bundles JS (vérifié
— et de toute façon reverse-engineered, contraire au principe du 18/08).
→ **API MusicKit officielle** : recherche catalogue avec un simple token
développeur (JWT ES256 signé avec une clé MusicKit du portail Apple
Developer — pas de login utilisateur pour le catalogue). Signature faite
avec `cryptography` (déjà une dépendance aiortc), token en cache 12 h.

- Chaîne playlists désormais : alias → favoris Sonos → playlists NAS →
  **catalogue Apple Music (MusicKit)** → Spotify explicite → refus.
  « sur Spotify » explicite saute l'étape Apple. Sans
  `data/musickit.json`, l'étape se désactive proprement (comme Spotify
  sans credentials).
- Config attendue (action Fred) : portail développeur → Certificates,
  Identifiers & Profiles → Keys → nouvelle clé **MusicKit** → télécharger
  le `.p8`, puis `data/musickit.json` :
  `{"team_id": "…", "key_id": "…", "private_key": "-----BEGIN PRIVATE KEY-----…"}`.
- Portée : playlists ÉDITORIALES du catalogue (« Disney Hits ») — les
  playlists perso Apple Music restent phase 3 (favoris Sonos en attendant).
- Tests : ordre complet favoris > NAS > Apple > Spotify ; génération du
  JWT vérifiée cryptographiquement (clé P-256 réelle, signature validée).

## 2026-08-19 — Playlists perso Apple Music : scrape Music.app + cache

Demande Fred : scraper ses playlists via AppleScript et les mettre en cache.

- `musicapp_playlists()` dans `_sonos_common.py` : `osascript` → Music.app
  (`name of user playlists whose special kind is none`, délimiteur
  linefeed — les noms peuvent contenir des virgules), **227 playlists**
  scannées en vrai. Cache disque quotidien `data/musicapp-playlists.json`
  (même TTL que le NAS), rafraîchi par le même bouton « 🔄 NAS » (le scan
  Music.app y est best-effort : son échec ne casse pas le re-scan NAS).
- Rôle : **résolution seulement** — pas de lien de partage public pour une
  playlist perso, la lecture reste phase 3 (AirPlay). Placée AVANT le
  catalogue MusicKit dans la chaîne playlists : « ma playlist jogging »
  reconnue → réponse honnête + conseil favori Sonos, et le catalogue ne
  peut pas la détourner avec une playlist éditoriale au nom proche.
  Chaîne finale : alias → favoris → NAS → perso Music.app (réponse) →
  catalogue MusicKit → Spotify explicite → refus.
- **TCC en attente (action Fred, à l'écran du Mac)** : le scrape depuis le
  PROCESS DU BOT échoue en AppleEvent timeout -1712 — le consentement
  Automation (« Python » → Musique) ne peut pas s'afficher écran
  éteint/verrouillé (même mécanique que Réseau local le 19/08, mais en
  timeout au lieu d'Errno 65). En attendant : le cache disque (scanné
  depuis un shell autorisé) sert le résolveur ; seul le volet Music.app du
  bouton 🔄 est inopérant. Re-déclencher le prompt : cliquer 🔄 NAS avec
  l'écran déverrouillé, puis Autoriser.
- Vérifié bout-en-bout à la voix : « Mets ma playlist Autumn Break 25 » →
  reconnue, réponse honnête, rien joué. Nota : la première conversation
  après un restart du bot a ~35 s de latence LLM (la sonde a fermé avant
  la réponse — tour « dangling », pas un bug du plugin).

## 2026-08-19 — SMAPI/AppLink (sonos.svrooij.io) : évalué et écarté (lead parqué)

Seule voie vers la recherche Apple Music via le compte lié aux enceintes
(playlists perso incluses, résultats jouables nativement) : le protocole
SMAPI + auth AppLink documenté par svrooij (implémentable en Python, pas
de démon Node). Écarté malgré tout : (1) API partenaire
reverse-engineered — c'est la rotation DeviceLink→AppLink qui a cassé le
support Apple/Spotify de SoCo, interdit sur le service principal
(DECISIONS 18/08, réaffirmé 2×) ; (2) la lecture des résultats SMAPI
exige URI+DIDL en UPnP direct → second chemin de contrôle (contraire au
plan de contrôle unique) ; (3) le manque est déjà couvert (favoris Sonos
+ scrape Music.app + phase 3 AirPlay + MusicKit catalogue).
**Déclencheur de réouverture** : le flux « étoiler en favori » s'avère
pénible au quotidien ET l'AirPlay de la phase 3 trop dépendant du Mac —
alors SMAPI, cantonné à la recherche de playlists perso, favoris en
secours permanent.

## 2026-08-19 — Analyse de la session de l'après-midi (invités, démos Sonos)

Trois retours de Fred, analysés sur logs + transcripts (16:05–16:13) :

- **Latences.** Les outils étaient rapides (sonos_musique 1,2–1,8 s,
  sonos_controle 0,04–0,7 s). Deux mécanismes réels : (1) « Joue le
  dernier album d'Ed Sheeran » = 12 s ressenties — 3 passes LLM
  (web_search « dernier album » 3 s → question de pièce → lecture),
  inhérent au tour multi-étapes ; (2) **une génération LLM de 24 s**
  (« Séjour » → grouper, 16:08:52→16:09:16, une seule passe, pas de
  restart) pendant un brouhaha continu : VAD/Whisper/zipformer/embeddings
  tournaient toutes les 1–3 s sur le même GPU que le décodage qwen, plus
  une interruption d'agrégation juste avant. Hypothèse dominante :
  contention GPU en conversation chargée (+ re-prefill après
  interruption). **Lead** : instrumenter le TTFT par tour (métriques
  pipecat) pour confirmer avant tout tuning. Rappel : ~35 s sur le tout
  premier tour après un restart du bot (vu 2×).
- **Mode privé impossible à quitter** (16:09:57→16:12:26, sorti par
  reconnexion). Causes empilées : la levée vocale exige la barre PLEINE
  0.60 sans leniency (design) alors que ses éveils du jour scoraient
  0.38–0.88 selon l'acoustique ; les refus de levée n'étaient PAS
  journalisés (indiagnosticable) ; le bouton 🤫 (/api/stop) matchait « 0
  session » car enter_hold délie l'activateur ; et il n'existait AUCUNE
  sortie HTTP. **Corrigé** : (1) refus de levée journalisés (motif
  seulement, jamais le contenu — sim/durée) ; (2) **POST /api/resume** +
  bouton « 🔔 Réveiller » (toujours visible) : lève le hold de toutes les
  sessions retenues — non scopé (plus d'activateur à qui scoper), token =
  autorité du foyer comme le 🤫. La barre vocale pleine reste inchangée
  (le durcir moins = affaiblir le hold ; l'issue garantie est le bouton).
- **Données de calibration au passage** : le canal d'éveil brut a ouvert
  deux échanges sans « Merlin » dans la transcription (« Merci. » accepté
  comme éveil sim=0.88, puis « Bonjour, Baptiste. » répondu) — faux
  éveils en environnement bruyant, grain à moudre pour l'item ROADMAP
  « vrai modèle d'éveil ». Les clôtures polies ont fonctionné en vrai
  (2 « Merci. » tiers ignorés, « Merci ×4 » de l'activateur a fermé).
- **UI** : fil de conversation inversé (demande Fred) — dernier message
  EN HAUT (insertion en tête + accroche au sommet ; la bulle bot en
  streaming se place au-dessus de sa bulle utilisateur), les boutons
  restent visibles sans scroller.

## 2026-08-19 — Retour du « Merci. » fantôme : la musique dans le micro

Fred voit « beaucoup de Merci que je n'ai pas dits ». Mesuré dans
transcripts.db (tours « merci nu » / tours utilisateur par jour) :
22/162 le 13/08 (jour du diagnostic originel, avant le fix VAD 0.3→0.6),
0–1/jour du 14 au 18, **33/161 le 19/08 — premier jour de lecture Sonos**.
Mécanisme : les voix chantées des enceintes passent légitimement le VAD
0.6, et Whisper transcrit la musique en « Merci. » (l'artefact
sous-titres français documenté le 13/08 — no_speech_prob=0.00, les
filtres de probabilité ne peuvent pas l'attraper). Nouveau régime
acoustique : l'assistant CRÉE désormais le bruit de fond qu'il doit
ignorer. Impact réel : 23 drops hors attention (inoffensifs, dataset
STT), 7 « Merci. » acceptés en cours d'échange, et un « Merci. ×4 »
fantôme qui a FERMÉ l'échange de Fred (passé la barre indulgente 0.35
contre l'ancre — coloration même pièce/micro).

- **Fix appliqué : mot identique ×3+ = hallucination** (extension de la
  boucle de répétition, qui exigeait ≥8 mots) : « Merci. Merci. Merci. »
  filtré ; un mot doublé (« oui oui », « merci merci ») reste valide —
  ça se dit ; triplé, c'est Whisper qui transcrit les enceintes. Coût
  assumé : un « oui oui oui » enthousiaste serait filtré (tour perdu >
  action fausse).
- **NE PAS toucher** : VAD 0.6 (le monter contre la musique coûterait la
  vraie voix lointaine), barre des clôtures (le ×3+ couvre le footgun).
- **Lead parqué : gating conscient de la musique** — Merlin sait qu'il a
  lancé la lecture ; pendant qu'une enceinte joue, exiger une
  vérification plus stricte (ou le mot d'éveil) pour les tours courts.
  À rouvrir si les fantômes persistent malgré le fix ×3.

## 2026-08-21 — MusicKit : parqué (pas d'abonnement développeur pour l'instant)

Fred ne souhaite pas (encore) payer le programme développeur Apple — la
clé MusicKit l'exige, et il n'existe AUCUNE alternative viable pour la
recherche de playlists catalogue (iTunes Search : pas d'entité playlist ;
token web anonyme : plus extractable ; SMAPI : rejeté par principe —
tout vérifié le 19/08). Le résolveur reste en place, dormant sans
`data/musickit.json` (il se réveille tout seul si une clé arrive).
Manque résiduel : les playlists ÉDITORIALES Apple à la voix — palliatif
gratuit : les étoiler une fois dans l'app Sonos (favoris). Réouverture :
Fred prend un compte développeur, ou l'usage réclame souvent des
playlists catalogue à la voix.

## 2026-08-21 — Notifications : Telegram prioritaire (iMessage vers soi-même = silencieux)

Incident : les alertes du monitor et de l'atelier arrivaient bien dans
Messages mais SANS notification sur l'iPhone. Cause : iOS supprime la
bannière pour les messages envoyés depuis son propre Apple ID vers
soi-même (traités comme des messages synchronisés depuis un autre
appareil) — comportement système, pas un bug de `notify.py`. C'est un
angle mort de la validation du 16/08 : on avait vérifié « sent » côté
envoi, jamais la bannière côté réception.

- **Telegram Bot API retenu comme canal prioritaire** (déjà documentée le
  16/08 comme alternative robuste) : long-polling côté entrant futur, pas
  de port ouvert, notifications normales. Coût assumé : un serveur tiers
  (Telegram) voit le contenu des alertes — acceptable, ce sont des états
  de santé du stack et des cycles d'atelier, jamais de transcription.
- **iMessage conservé en fallback** dans `notify.send()` : si Telegram
  est absent de la config OU échoue (réseau), on retente iMessage. Même
  contrat best-effort ("sent"/"disabled"/"failed", ne lève jamais,
  timeout 15 s par canal). Implémentation stdlib (`urllib`), zéro
  dépendance ajoutée.
- **Config** : `data/notify.json` `{"telegram": {"token", "chat_id"}}`
  (git-ignoré comme le reste de `data/`), env
  `MERLIN_NOTIFY_TELEGRAM_TOKEN`/`MERLIN_NOTIFY_TELEGRAM_CHAT` priment.
  Helper one-shot `venv/bin/python notify.py chat-id` (getUpdates) pour
  découvrir le chat_id après avoir écrit au bot.
- **Lead réouvert à moindre coût** : l'approbation entrante (item 7 du
  backlog) devient plus simple — le bot Telegram existe désormais côté
  sortant, il ne manque que le long-polling + allowlist chat_id + slug
  explicite dans la réponse.

## 2026-08-21 — Inscription guidée : script de conditions, pas de phrases « magiques »

Préparation à l'inscription d'un second profil (femme de Fred). Question
posée : faut-il un jeu de phrases prédéfinies ?

- **Constat** : le gate est indépendant du texte (embeddings moyennés,
  similarité cosinus) — le contenu des phrases n'apporte rien. Ce qui manque
  à un profil neuf, c'est la diversité ACOUSTIQUE : distance, volume,
  prosodie, pièce. Un script lu assis face au téléphone reproduirait le
  profil mono-session qui a causé le faux rejet « marées » du 14/08
  (énoncé réel 4 s scoré 0.37).
- **Décision** : `voice_profile.py enroll` (neuf ET top-up) imprime
  `ENROLL_SCRIPT` — 8 phrases françaises, chacune associée à une condition
  (1 m voix normale, phrase longue, 2–3 m, voix douce, question montante,
  ton d'ordre, pièce d'usage, en mouvement dos au téléphone). Rôle réel des
  phrases : garantir que chaque énoncé passe les filtres d'inscription
  (≥ 1,2 s, ≥ 3 mots — le bavardage libre produit des « Oui. » qui ne
  comptent pas) et lever le « je dis quoi ? » d'un non-initié.
- **Contraintes respectées par les phrases** : aucune ne contient
  « chut »/« stop » (phrase d'arrêt) ; aucune ne déclenche d'action réelle
  (pas de Sonos/musique) ; « Merlin » en tête des six premières, les deux
  dernières comptent sur la fenêtre de suivi (note dans le script : redire
  « Merlin » après ~10 s).
- **Rappels opérationnels dans le script** : seul(e) dans la pièce (garde
  anti-contamination sim < 0.30) ; ne pas viser la session parfaite —
  l'adaptation par l'ancre et le top-up complètent en usage réel ; en cas
  de faux rejets la première semaine : top-up, PAS de baisse de seuils
  (préférence ferme : rater vaut mieux que se tromper).
- **Script poussé sur le téléphone** (demande Fred, même jour) : à
  l'ouverture de l'inscription (neuf et top-up), `voice_profile.py` envoie
  le script via `notify.send()` — plus pratique à lire en se déplaçant
  (conditions 3, 7, 8) qu'un terminal. Best-effort comme tout `notify` :
  l'inscription s'ouvre même si l'envoi échoue, le statut est imprimé.
  Message ~1,1 k chars, sous le plafond Telegram (4 096). Testé réel :
  « sent », reçu sur le téléphone.

## 2026-08-21 — Incident alertes Telegram : éviction du modèle épinglé par Hermes → Hermes retiré

Symptôme : rafales de « ⚠️ Merlin en panne : LLM épinglé » / « ✅ rétabli »
en Telegram (le monitor flappait). Chaîne causale mesurée :

1. Home Assistant poussait CHAQUE changement d'état d'entité (Sonos Roam en
   charge, interrupteurs…) vers le gateway Hermes (`~/.hermes`, :8642),
   qui appelait Ollama avec le tag de BASE `qwen3.6:35b-a3b-q4_K_M`
   (ctx 262 k, 28,5 Go) — pas le tag `-ctx32k` épinglé.
2. Charger ce gros modèle évincait le modèle épinglé de Merlin (les deux ne
   tiennent pas ensemble en RAM). Les appels Hermes ne passaient pas de
   `keep_alive` → déchargement après 5 min → plus AUCUN qwen chargé.
3. Le check monitor `api/ps | grep qwen` passait alors à FAIL → alerte ;
   l'événement HA suivant rechargeait le modèle → « rétabli ». Flapping.
   Effet de bord : Merlin repartait en cold start 6–7 s (le pin ne se
   refait qu'au démarrage du bot).

**Décision (demande Fred : « je n'utilise plus Hermes ») : Hermes retiré.**
- Services arrêtés + LaunchAgents supprimés (`ai.hermes.gateway`,
  `com.hermes.router`, `com.hermes.webui`) ; plists archivés dans
  `~/hermes-retired-20260821/`. Ports 8642/8101 libres, vérifié.
- Répertoires SUPPRIMÉS (choix Fred, « delete everything ») : `~/.hermes`
  (2,7 Go — mémoires agent, kanban, sessions), `~/hermes-router`,
  `~/Developer/hermes-tools`. Seuls les plists archivés subsistent.
- Bot relancé (`kickstart`) : modèle `-ctx32k` ré-épinglé (expires 2318 =
  keep_alive -1), stack 4/4 ok. Aucune dépendance côté merlin-voice :
  la seule référence à :8642 dans `bot.py` est un exemple commenté.
- Caduque : le « rôle futur = worker de fond » d'Hermes (décision du
  12-13/08). Toute délégation future partira sur autre chose.
- Côté HA (vérifié le même jour via l'API REST + websocket) : AUCUNE
  automatisation ni rest_command ne poussait vers :8642 — le trafic
  d'événements était un abonnement websocket ouvert PAR Hermes (sens
  inverse de l'hypothèse initiale), mort avec lui. Seule trace restante :
  le token longue durée « hermes », révoqué (`auth/delete_refresh_token`).
  Tokens restants : « Merlin » (celui de `data/ha-token`) + sessions
  navigateur/mobile non nommées, intactes. Stack 4/4 ok après révocation.

**Monitor corrigé (même jour)** : le check « LLM épinglé » greppait `qwen`
— il répondait « ok » quand seul le MAUVAIS modèle (tag de base) était
chargé. Resserré sur le tag exact `"qwen3.6:35b-a3b-q4_K_M-ctx32k"`
(= défaut `LLM_MODEL` de bot.py ; à changer en même temps si le modèle
change). Vérifié : 4/4 ok avec le tag épinglé.

## 2026-08-21 — « Merlin plus puissant » : escalade cloud opt-in, pile Kyutai triée (analyse, rien d'implémenté)

Besoin exprimé (Fred) : le modèle local ne suffit pas toujours —
conversations plus « challengeantes », et construire des workflows en
parlant à Merlin plutôt qu'en session Claude Code. Session d'analyse
uniquement (aucun code) ; verdicts à ne pas re-dériver :

**Architecture retenue : deux étages, cloud opt-in par tour.** qwen reste
le cerveau vocal (calibré latence/outils, hors ligne, privé) ; la
profondeur s'obtient par un outil d'escalade explicite, pas en remplaçant
la boucle principale.

- **Plugin `demande_a_claude` (à faire, priorité de ce fil)** : outil
  appelé sur demande explicite (« demande à Claude », « réfléchis
  vraiment ») → appel d'un modèle cloud en streaming, réponse parlée
  phrase par phrase derrière une phrase-pont (comme les fillers plugins).
  Seuls les tours explicitement escaladés quittent la machine (cohérent
  avec la philosophie du gate) ; inactif hors activateur Fred / en mode
  famille. Backend en env (`MERLIN_ESCALATE_*`) pour A/B sur usage réel :
  Claude Opus 5 (meilleur partenaire de débat, ~5/25 $ par MTok — un
  échange vocal = quelques centimes) ou Gemini 3.1 Pro (réutilisation
  possible des credentials agy). Fallback zéro-credential : `agy -p` /
  `codex exec` headless (perd le streaming, +qq s de démarrage CLI —
  acceptable pour « réfléchis bien », mauvais pour le débat).
- **Atelier : pas de worker `claude` ajouté** (décision Fred) — agy/codex
  suffisent. Le levier qualité de l'atelier reste la spec (dialogue de
  raffinement vocal avant dispatch = lead, pas décidé).
- **Modèles speech-to-speech temps réel (GPT-Realtime-2 05/2026, Gemini
  3.1 Flash Live 03/2026) : écartés comme boucle principale.** Ils
  exigent un flux audio CONTINU vers le cloud : rupture frontale avec
  voice_guard (famille et tiers streamés chez OpenAI/Google, gate et mode
  privé décoratifs) + tarification à la minute. Lead parqué : « mode
  débat » opt-in en pipeline parallèle — pipecat 1.3.0 embarque déjà
  `services/openai/realtime` et `services/google/gemini_live` ; le gate
  local authentifie et ouvre la session, le canal brut « Merlin stop »
  (qui reste local) la tue. Exige sa propre décision privacy AVANT tout
  code.

**Pile Kyutai (creusée le même jour) — trois verdicts distincts :**

- **Unmute : écarté.** C'est la couche d'orchestration (STT → LLM
  OpenAI-compat → TTS en websockets) — rôle déjà tenu par Pipecat — et le
  déploiement est CUDA/x86_64 uniquement, macOS explicitement non
  supporté. Rien à adopter au-delà des idées. Ce sont les MODÈLES
  dessous qui comptent (backends MLX dispo, code MIT/Apache, poids STT
  CC-BY 4.0).
- **Pocket TTS (01/2026, 100 M params) : candidat n°1 de l'audition
  TTS**, passe devant Chatterbox dans le banc. Temps réel sur CPU (donc
  SOULAGE la contention GPU au lieu de l'aggraver), français, clonage de
  voix (une voix propre à Merlin). Test cheap : RTF CPU + oreille sur le
  français vs Kokoro ; Kokoro reste le fallback derrière une env var.
- **Kyutai STT (`stt-1b-en_fr`, delayed streams modeling) : stratégique
  mais BLOQUÉ derrière l'instrumentation TTFT.** Gains : transcription en
  continu avec 0,5 s de délai (le LLM part ~0,5 s après le dernier mot,
  contre silence-Silero puis Whisper batch aujourd'hui), VAD sémantique
  (fin de tour prédite, pas devinée à l'énergie — moins de coupures sur
  pause mi-phrase), timestamps mot à mot (utile au dataset de tuning).
  Risques : un 1B qui transcrit EN CONTINU sur le même GPU que le qwen
  épinglé = le scénario exact de la génération à 24 s du 19/08 (jamais
  instrumenté) ; tension avec l'item « gating de Whisper hors attention »
  (privacy/compute vs always-on — le VAD sémantique peut réconcilier, à
  trancher) ; intégration = service Pipecat custom à écrire, miroir de
  `WhisperSTTServiceMLX` (pas de service Kyutai local dans pipecat
  1.3.0 ; `gradium` = le cloud commercial de Kyutai, écarté : l'audio
  quitterait la machine). Les profils voix (CAM++) sont indépendants du
  STT, non touchés.
- **Unification du moteur d'éveil : lead séparé, PAS dans la migration
  STT.** Un STT streaming pourrait absorber éveil + stop + transcription
  (le zipformer n'existe que parce que Whisper ne streame pas), mais le
  canal brut est calibré et porteur (stop ~20 ms) — une seule bascule à
  la fois.

**Séquence décidée** : `demande_a_claude` → audition Pocket TTS →
instrumentation TTFT + jeu de test transcripts.db (~20 énoncés) → banc
Kyutai STT. Chaque étape shippable et abandonnable indépendamment.

## 2026-08-21 — Outil `home_assistant` : lumières + scènes, périmètre calé sur les entités réelles

Item de la revue du 13/08 (« l'upgrade quotidien le plus visible »).
Plugin `plugins/home_assistant.py`, même plan de contrôle que Sonos : REST
du HA Yellow, jamais d'accès direct aux ampoules.

- **Périmètre = inventaire réel de HA, pas la promesse « lumières/volets ».**
  Relevé du 21/08 : 22 entités `light` (Hue, dont les groupes de pièce
  Cuisine/Chambre/Dressing/Entrée), 17 `scene`, **zéro `cover`, zéro
  `climate`**. Les 38 `switch` sont quasi tous du bruit Sonos
  (crossfade/loudness/TV autoplay) → domaine exclu volontairement. Volets et
  thermostat : à ajouter le jour où les entités existeront, pas avant.
- **Client HA générique extrait dans `plugins/_ha_common.py`** (URL/token,
  `ha_request`, `norm`, `friendly`) — c'était le « client réutilisable »
  promis par la phase 1 de `docs/SONOS.md`. `_sonos_common.py` les
  ré-importe et ré-exporte : appelants et tests (`common.ha_request`
  monkeypatché) inchangés, suites sonos_controle/sonos_musique vertes.
- **Même gate que Sonos (préférence Fred : ne pas agir > agir de travers)** :
  match franc = nom exact, mêmes mots dans un autre ordre (« lecture
  dressing » → scène « Dressing Lecture »), sous-chaîne unique, ou unique
  voisin difflib (cutoff 0.75). Plusieurs candidats → aucune action, on
  renvoie la liste. Vérifié en réel que le gate dépend de l'inventaire
  joignable : « suspension » est ambigu dans les tests (2 candidates) mais
  franc dans la maison actuelle (les autres suspensions sont
  `unavailable`) — comportement voulu.
- **Exclusions du pool** : entités `unavailable` (interrupteur mural coupé)
  et les LED d'appareil (`light.home_assistant_voice_*`) — on ne propose
  jamais à la voix une lumière injoignable ou technique.
- **« Tout »** (`tout/toutes les lumières/partout…`) accepté pour
  allumer/éteindre uniquement — un seul appel de service avec la liste
  d'entités. Pas de « tout » pour la luminosité ni les scènes.
- **Luminosité** : échelle parlée 0–100 (`brightness_pct` côté HA,
  `brightness` 0–255 lu en retour) ; « plus »/« moins » = ±20 déterministe
  (pendant du ±10 volume Sonos) ; cible 0 → `turn_off`. Les lumières
  on/off simples n'exposent pas `brightness` → champ omis du statut (pas de
  faux « 0 % » lu à voix haute).
- **Un seul `GET /api/states` par invocation** (pools lumières + scènes +
  états) — pas de cache : l'appel LAN est court et l'état des lumières
  change tout le temps, contrairement aux entités Sonos (cache 10 min).
- **Prompt système inchangé** : comme pour Sonos, la description du schéma
  suffit au routage de l'outil.
- Tests offline : `tools/test_home_assistant.py` (fake HA enregistreur,
  7 cas). Vérifié en réel : statut (7 lumières allumées, luminosités
  cohérentes), allumer/éteindre « Suspension Dressing », remise à l'état
  initial.

## 2026-08-21 — Incident soirée : rafale de faux rejets de Fred (musique + loin champ), le seuil 0.60 tient

Session vocale 19:15–19:28, musique Sonos en cours dans la cuisine (volume
12–22) pendant quasiment tout l'échange. ~15 tours de Fred rejetés à tort,
tous mesurés dans `data/merlin.log` (grep `VoiceGate`) et `transcripts.db` :

- **Sims des tours rejetés** (meilleur profil = fred ou « voix inconnue ») :
  0.10, 0.17, 0.18, 0.19, 0.25, 0.30, 0.31, 0.31, 0.34, 0.36, 0.39, 0.40,
  0.45, 0.56, 0.57. Plage propre de Fred : 0.72–0.89. La quasi-totalité
  tombe DANS la plage mesurée de sa femme sur le même téléphone (0.08–0.54)
  → **ne PAS baisser le seuil 0.60**, il n'existe aucun seuil qui accepte
  ces tours sans accepter aussi un tiers. Confirme la décision du faux
  rejet à 0.45 (même cause : loin champ + musique).
- **Motif UX le plus coûteux** : l'éveil court « Salut Merlin ! » passe
  (0.65–0.75, leniency tours courts) puis la COMMANDE complète qui suit est
  rejetée (« pas l'activateur (fred) ») — Merlin salue puis ignore l'ordre,
  Fred répète 2–3 fois. L'embedding du tour long est plus contaminé par la
  musique que celui du tour court prononcé plus fort/plus près.
- **Réponse (inchangée, maintenant urgente)** : top-up de diversité du
  profil (`tools/voice_profile.py enroll fred`, 24 embeddings actuels,
  consistency mean 0.85) **exécuté dans les conditions qui échouent :
  cuisine, distance d'usage, MUSIQUE EN COURS à volume normal**. Lead :
  l'`ENROLL_SCRIPT` (8 conditions) ne comporte pas de condition « musique en
  fond » — la donnée de ce soir dit que c'est LA condition dominante
  d'échec ; à ajouter au script si le top-up manuel ne suffit pas.

## 2026-08-21 — Top-up de Fred fait (musique + cuisine) ; bug : un top-up au cap ne se fermait jamais

Suite de l'incident du soir. Fred a exécuté le top-up dans la cuisine,
musique en cours — 10 énoncés inscrits entre 19:33 et 19:36.

- **Le top-up a bien atterri** : `fred.npz` réécrit, consistency
  min 0.72→0.52, mean 0.85→0.75 — la queue basse (0.52–0.67) EST la
  diversité musique/loin champ recherchée ; distribution saine, pas
  d'aberrant (item « prune » non nécessaire ici).
- **Bug découvert** : `voice_profile.py` ouvre un top-up avec une cible
  ABSOLUE (`count+8` = 32) alors que le profil est plafonné en anneau à
  `PROFILE_MAX` = 24 (`enroll()` évince le plus ancien). `profile.count`
  reste à 24, la cible 32 est inatteignable → **le marqueur `.enrolling` ne
  se ferme jamais**. Conséquence grave : marqueur ouvert = toute voix pas
  franchement différente (sim ≥ 0.30 — la femme de Fred mesure jusqu'à
  0.54) est inscrite dans le profil ET répondue comme l'inscrit. Le
  marqueur est resté ouvert ~40 min (le mode privé « Merlin chut » de
  19:36 a limité l'exposition). Fermé à la main (`cancel`).
- **Correctifs (`voice_guard.py`)** :
  1. **Progression de top-up = énoncés inscrits depuis l'ouverture**,
     compteur persisté en 3ᵉ token du marqueur (`fred 32 3`) — survit à un
     restart du bot, format rétro-compatible (les lecteurs prennent
     token[0]/[1]). Complet quand compteur ≥ cible − PROFILE_MAX.
     L'inscription fraîche (cible 8 ≤ cap) reste au comptage absolu.
  2. **TTL du marqueur** (`ENROLL_PENDING_TTL` = 1 h) : un marqueur
     abandonné expire au lieu de rester une porte ouverte. Chaque énoncé
     inscrit réécrit le marqueur (mtime rafraîchi) — une session active
     n'expire jamais.
- Tests : `test_topup_rolling_cap_and_stale_marker` (complet en 8, reprise
  après restart, expiration) dans `tools/test_voice_guard.py` — 10/10 ok.
- Reste à vérifier à l'usage : le gain réel en conditions musique (les sims
  des prochaines sessions cuisine, via grep VoiceGate).

## 2026-08-22 — Incident majeur : le top-up « musique » a rendu le profil poreux → emballement de l'adaptation → profil de Fred réinitialisé

**Le verdict d'hier est inversé : un top-up en conditions bruyantes est un
poison, pas un remède.** Chronologie mesurée (`data/merlin.log`) :

1. Top-up du 21/08 au soir (10 embeddings musique/loin champ) → le profil
   de Fred devient poreux : la séparation mesurée le 13/08 (femme :
   0.08–0.54 sur le même téléphone) s'effondre.
2. Matinée du 22/08 : la famille est acceptée comme « fred » toute la
   matinée (sims 0.61–0.86 ; confirmé par Fred : au moins sa femme et son
   fils). **Mauvaises acceptations = l'échec que le gate doit préférer
   éviter.** Chaque accept ≥ ADAPT_SIM (0.75) a ADAPTÉ l'embedding
   étranger dans le profil (~une dizaine dans la journée), le cap anneau
   (PROFILE_MAX 24) évinçant à chaque fois un embedding d'origine →
   boucle d'emballement (plus poreux → absorbe plus).
3. Inscription de Camille bloquée à 3/8 : le profil pollué de Fred volait
   ses phrases du script à 0.79–0.82 (Fred n'a pas parlé du tout).
4. Leçon de méthode : la vérification « les 2 derniers embeddings sont
   fred-like » du 22/08 13h49 était CIRCULAIRE (comparée à une base déjà
   polluée) — le témoignage de Fred a corrigé le diagnostic.

**Décisions :**
- **Les profils s'inscrivent en conditions calmes uniquement.** La réponse
  aux faux rejets musique n'est PAS la diversité de profil en bruit : c'est
  le tour perdu assumé (préférence Fred) ou un micro dédié (lead roadmap).
  Ne plus jamais recommander un top-up « musique en cours ».
- **Gardes d'adaptation** (`voice_guard.py`, testées) : (1) marge
  inter-profils `ADAPT_MARGIN` 0.10 — pas d'absorption si un autre profil
  inscrit score presque autant ; (2) gel total de l'adaptation pendant une
  inscription ouverte ; (3) chaque adaptation (ou refus) est journalisée —
  l'emballement d'aujourd'hui était invisible faute de log.
- **Profil de Fred réinitialisé** (irrécupérable : rien d'origine ou
  presque ne restait ; état pollué sauvegardé
  `data/voices/fred.npz.bak-20260822-1350`). Ré-inscription au calme via le
  script. Les 3 embeddings de Camille sont sains (cohérence 0.76–0.79) et
  conservés ; elle complète ses 5 phrases après Fred.
- Séquelle connue : les attributions `speaker=fred` du 22/08 matin dans
  `transcripts.db` sont fausses (femme/fils) — laissées en l'état,
  consommateurs avertis.
