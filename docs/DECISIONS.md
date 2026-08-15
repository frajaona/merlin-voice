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
