# Journal des décisions

Chaque décision clé, avec sa justification et les données mesurées derrière.
Ne pas modifier une valeur calibrée sans nouvelles données — les logs et
`data/transcripts.db` sont la source pour re-calibrer.

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
