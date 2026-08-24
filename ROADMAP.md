# Merlin — roadmap d'améliorations

Suivi des améliorations progressives. Mis à jour à chaque session de travail.
(Historique détaillé et calibrations : voir les logs git et `data/merlin.log`.)

## Fait

- **2026-08-13** — Garde vocale (`voice_guard.py`) : gate locuteur (CAM++), gate
  d'attention (mot d'éveil + fenêtre de suivi), filtres d'hallucinations Whisper,
  VAD 0.3→0.6, Whisper turbo Q4→fp16, seuil locuteur calibré 0.60 sur données
  réelles (Fred 0.72–0.89, autre voix même téléphone 0.08–0.54).
- **2026-08-13** — Profils par personne (`data/voices/<nom>.npz`) + liaison à
  l'activateur : celui qui dit « Merlin » possède l'échange ; « Merlin… » passe
  le micro. Mode famille optionnel (`MERLIN_FAMILY_MODE=1`).
- **2026-08-14** — Moteur d'éveil audio brut (`wake_word.py`) : zipformer
  français en streaming sur tout l'audio micro, second canal d'éveil en OU avec
  la transcription Whisper. Validé sur audio réel (2/2 éveils).
- **2026-08-14** — Top-up d'inscription (`voice_profile.py enroll <nom>` sur un
  profil complet ajoute de la diversité) + adaptation confirmée par l'ancre
  (le profil apprend la voix lointaine/douce en cours d'usage).
- **2026-08-15** — `temperature` 0,2 par défaut dans bot.py (env
  `LLM_TEMPERATURE`) : fiabilité web_search 14/15 contre ~60 % à temp 1
  (mesures dans `docs/DECISIONS.md`).
- **2026-08-15** — **A/B tranché : qwen confirmé.** mistral-small3.2 recalé
  sur données de production (médiane 1,54 s vs 0,49 s jusqu'à la première
  phrase, croissant avec le contexte — prefill dense). Verdict et leçon de
  protocole dans `docs/DECISIONS.md`. Modèles éliminés supprimés du disque.
- **2026-08-15** — **Cold start Ollama corrigé** : `_preload_llm()` au
  démarrage épingle le modèle (`keep_alive:-1`, endpoint natif) — plus de
  rechargement de 6–7 s au premier tour d'une session.
- **2026-08-15** — **KV cache 262 K → 32 K** (tag dérivé
  `qwen3.6:35b-a3b-q4_K_M-ctx32k`) : −5 Go résidents (28 → 23 Go), TTFT
  identique mesuré avec `tools/bench_longctx.py` (nouveau bench conforme au
  protocole : tour long ~10 k chars, réglages de prod). **Historique de
  session borné** (`HistoryTrimmer`, `MERLIN_MAX_HISTORY_MSGS=40`) — la
  liste de messages ne grandit plus sans limite. **requirements.txt
  épinglé** (pipecat 1.3.0 et al.). Détails dans `docs/DECISIONS.md`.

- **2026-08-16** — **Notifications iMessage sortantes de l'atelier**
  (`notify.py`, AppleScript → Messages.app) : lancement de construction,
  échec, candidat prêt (avec la phrase d'activation). Désactivé tant que
  `data/notify.json` (`{"imessage": "+336…"}`) ou `MERLIN_NOTIFY_IMESSAGE`
  n'est pas renseigné ; best-effort (jamais bloquant). Test hors-ligne :
  `tools/test_notify.py`. WhatsApp écarté, approbation entrante différée —
  voir `docs/DECISIONS.md`.

- **2026-08-16** — **Chaîne de workers de l'atelier réparée** : le fallback
  codex était cassé (`codex exec --full-auto` supprimé du CLI) — remplacé
  par `--sandbox workspace-write` (vérifié codex-cli 0.147.0, `run_worker`
  testé en vrai : rc=0). agy re-testé OK (le timeout 900 s du 16/08 était
  transitoire, modèle et flags toujours valides). La demande de test ayant
  échoué a été retirée de la file. Détails dans `docs/DECISIONS.md`.

- **2026-08-17** — **Dashboard web + auth de `/api/offer`** (items 7 et Ops
  « endpoint ouvert »). Auth : token partagé (`MERLIN_TOKEN` ou
  `data/auth-token` auto-généré, `Authorization: Bearer`) sur `/api/offer`
  et `/api/workshop*` (`dashboard_api.py`). Dashboard : `static/index.html`
  réécrit en **vanilla JS sans build** (décision contre le Voice UI Kit
  React — voir `docs/DECISIONS.md`) — RTVI activé sur le PipelineWorker,
  panneaux : décisions VoiceGate en direct (`RTVIServerMessageFrame` émis
  par le gate), transcript attribué, cartes d'outils (report level FULL),
  file de l'atelier avec activation (POST `/api/workshop/approve`, même
  preuve de gates que la voix/CLI). Setup téléphone : ouvrir
  `https://<host>:7860/#token=<token>` une fois. Tests :
  `tools/test_dashboard_api.py` (offline) ; sonde protocole bout-en-bout :
  `tools/probe_rtvi.py` (STT→gate→server-message vérifié sur audio réel).

- **2026-08-17** — **Chat texte dans le dashboard** : barre de saisie, même
  contexte/outils que la voix, toggle 🔊/🔇, bulles bot en streaming token
  par token (`bot-llm-text`). Le clavier contourne le VoiceGate à dessein
  (authentifié par le token, contrairement au micro). Envoi via
  `client-message` custom + mode skip_tts **collant** (le `send-text` natif
  de pipecat a un bug sur les tours à outils — la réponse « silencieuse »
  parlait ; trouvé par Fred en prod) ; retour au parlé au prochain tour
  vocal accepté ; `SilentTurnTTSFilter` coupe les fillers des plugins
  (« Je regarde ça. ») pendant les tours silencieux sans toucher aux alarmes
  différées. Journalisation : tours tapés en `[clavier]` + réponses via
  `AssistantResponseLogger` côté LLM (attrape aussi les réponses
  silencieuses). Vérifié bout-en-bout sur le scénario du bug
  (`tools/probe_rtvi.py "question météo"`, modes silencieux et parlé).
  Détails `docs/DECISIONS.md`. **Écarter une demande** depuis le panneau
  atelier (POST `/api/workshop/dismiss`, id = `ts`) : pending annulé, failed
  nettoyé, built rejeté (entrée skill-ready révoquée — plus activable ni par
  la voix ni par le CLI) ; building non défaussable ; `status: "dismissed"`
  conservé dans le jsonl (récupérable à la main), masqué du panneau.

- **2026-08-18** — **Attribution du locuteur dans les transcriptions**
  (item 4) : colonne `speaker` dans `turns` (migration in-place au démarrage),
  `GateCore.last_speaker` posé sur chaque chemin d'acceptation (NULL plutôt
  qu'un nom deviné : gate désactivé et fail-open ne s'attribuent pas ; les
  tours courts sont crédités à l'activateur ; en mode famille c'est la
  personne qui a parlé, pas l'activateur — corrige aussi le champ `speaker`
  du dashboard). Tests : `tools/test_transcript_store.py` (nouveau) +
  assertions dans `test_voice_guard.py` ; vérifié bout-en-bout via
  `probe_rtvi.py` (rejet → ligne `[filtré: …]` speaker NULL).

- **2026-08-18** — **LaunchAgents remis au propre + survie au reboot** :
  `com.merlin.warmup` supprimé (curl d'un :8101 mort), `com.merlin.monitor`
  réécrit (Ollama :11434, modèle épinglé via `/api/ps`, bot :7860 ; iMessage
  via `notify.py` sur transition ok↔fail seulement), **`com.merlin.bot`
  nouveau** (KeepAlive : démarre au login, relance sur crash — le bot survit
  au reboot). Source de vérité : `ops/` (`ops/README.md` pour l'installation
  et les commandes) ; `~/scripts/check-ai-stack.sh` = wrapper. Redémarrage :
  `launchctl kickstart -k gui/$(id -u)/com.merlin.bot`. `com.wyoming.*`
  laissés (possiblement utilisés par Home Assistant — à confirmer).

- **2026-08-18** — **Phrase d'arrêt + mode privé** (« Merlin chut », « Chut
  Merlin », « Merlin stop », env `MERLIN_STOP_WORDS`) : coupe la réponse en
  cours (~20 ms via le canal audio brut, le zipformer de l'éveil), ferme
  l'échange et met le gate en **mode privé** — tout est rejeté et *rien n'est
  journalisé avec contenu* jusqu'à une réactivation vérifiée (« Merlin, tu es
  là ? » par une voix inscrite, phrase complète : pas de leniency courte, et
  l'échec d'embedding ferme au lieu d'ouvrir — la seule inversion du
  fail-open). N'importe quelle voix peut arrêter (asymétrie inverse de
  l'éveil). Double canal comme l'éveil : brut (variantes mesurées
  CHU/CHUS/SUT/CHUTE/SHUT) OU transcription Whisper (« chut/chute/stop » +
  mot d'éveil). Vérifié bout-en-bout (probe : stop → « privé », la phrase
  suivante absente de transcripts.db). Détails et calibration dans
  `docs/DECISIONS.md`. Étape vers l'item « gating de Whisper hors attention »
  (Whisper tourne encore en mode privé, mais ne stocke plus rien).
  **Compléments (même jour)** : `MERLIN_STOP_ACTIVATOR_ONLY=1` (seul
  l'activateur peut arrêter en cours d'échange, barre indulgente ; le canal
  brut ne fait alors que couper le TTS, le hold attend la transcription
  vérifiée — défaut : n'importe quelle voix) ; **`POST /api/stop
  {"speaker": "<nom>"}`** (Bearer, scopé : seules les sessions dont cette
  personne inscrite est l'activateur — 404 si non inscrite, événement
  `gate-decision` poussé au dashboard) ; **bouton « 🤫 Chut »** dans le
  dashboard (HTTP, marche depuis un navigateur sans la session WebRTC ;
  envoie l'activateur courant appris du flux gate-decision). Tests :
  `test_voice_guard.py` (activator-only), `test_dashboard_api.py`
  (/api/stop scopé) ; vérifié en vrai (probe connectée + curl → hold +
  message reçu ; 401/400/404 vérifiés).

- **2026-08-18** — **Sonos phases 0–1** (`docs/SONOS.md`) : plugin
  **`sonos_controle`** (lecture/pause/suivant/précédent, volume
  absolu/relatif/muet, aléatoire, répétition, grouper/dégrouper, statut) via
  l'API REST de HA (`http://homeassistant.local:8123`, joignable, 401 sans
  token — token à créer par Fred → `data/ha-token`). Découverte des entités
  par template `integration_entities('sonos')` (fallback : tous les
  media_player), pièces en français flou (accents/articles), ambiguïté →
  refus d'agir (principe du gate). Tests hors-ligne :
  `tools/test_sonos_controle.py` (8 cas, fake HA). Monitor : check HA ajouté
  à `ops/check-ai-stack.sh` (actif seulement si `data/ha-token` existe).
  Phase 0 close (19/08, token fourni par Fred) : **7 enceintes inventoriées**
  (Séjour, Cuisine, Bureau, Chambre, Chambre Loulou, Chambre Gaby, Sonos
  Roam — attention : friendly names ≠ entity_ids, `media_player.bureau` =
  « Chambre » et `media_player.unnamed_room` = « Bureau » ; le plugin matche
  les friendly names, c'est le bon comportement) ; **verdict décisif phase
  2 : `play_media` HA relaie les liens de partage Apple Music ET Spotify**
  (albums testés en vrai sur la Roam à 5 % — pas de fallback SoCo
  nécessaire) ; **Music.app OK** (248 playlists, Sonos visibles en AirPlay
  2) après ouverture/permissions par Fred (les requêtes bibliothèque
  timeout-aient app fermée) ; **`com.wyoming.*` : CONSERVER** (le Yellow a
  des entités actives `stt.faster_whisper`/`stt.mlx_whisper`/`tts.piper`).
  URL HA épinglée par IP dans `data/ha-url` (mDNS `.local` échoue depuis le
  process launchd). **Dernier blocage** : autorisation « Réseau local »
  macOS pour « Python » (Réglages Système → Confidentialité → Réseau
  local) — **réglé le 19/08** (il y avait PLUSIEURS entrées Python dans le
  panneau ; c'est « Python » = `org.python.python`, le framework Homebrew,
  qu'il fallait activer, PUIS redémarrer le bot — voir `docs/DECISIONS.md`).
  **Phase 1 validée bout-en-bout en vrai le 19/08** via `probe_rtvi.py` :
  statut (« rien ne joue »), reprise de lecture dans la Cuisine (vérifiée
  playing côté HA), volume à 15 (vérifié 0.15), statut avec titre réel
  (Keen' V — Outété), pause (vérifiée). Reste : test à la voix par Fred en
  conditions réelles, puis phase 2 (`sonos_musique`).

- **2026-08-19** — **Clôture polie + fenêtre question resserrée** (réponse à
  l'incident du petit-déjeuner, voir `docs/DECISIONS.md` 19/08) : « Merci. »,
  « Merci Merlin », « Au revoir » pendant un échange **ferment l'échange**
  au lieu d'être répondus (`is_polite_closer` dans `voice_guard.py`, cœurs =
  remerciements/adieux uniquement — « oui/non/ok/d'accord » passent toujours
  la leniency courte ; **seul l'activateur peut clore** (barre indulgente
  SHORT_WAKE_SIM, profil ou ancre) — toute autre voix ou voix invérifiable
  est ignorée : ni réponse, ni fermeture). `MERLIN_QUESTION_SECS` 30 → 15 et
  le prompt système interdit les
  questions de politesse en fin de réponse (c'est elles qui armaient la
  fenêtre longue en continu). Tests : `test_polite_closer` dans
  `tools/test_voice_guard.py`. Reste côté utilisateur : inscrire la famille
  (item 1) et top-up du profil de Fred en conditions cuisine.

- **2026-08-19** — **Sonos phase 2 : plugin `sonos_musique`** (« joue X dans
  la pièce Y », voir `docs/SONOS.md`). Chaîne : alias du foyer
  (`data/sonos-aliases.json`) → iTunes Search (catalogue Apple Music) →
  Spotify Web API (explicite ou secours ; credentials à créer →
  `data/spotify-app.json`) → lien de partage → lecture native via HA
  `play_media`. Correspondance douteuse (< 0.60) → demande au lieu de
  jouer ; playlists sans alias → refus assumé (perso Apple Music = phase 3) ;
  « joue <artiste> » → son album le plus en vue, annoncé. Plomberie HA
  partagée extraite dans `plugins/_sonos_common.py` (sonos_controle
  refactoré dessus). Tests : `tools/test_sonos_musique.py` (8 groupes,
  faux HA/iTunes/Spotify). Vérifié en vrai bout-en-bout : « Joue l'album
  Discovery de Daft Punk dans la cuisine » → lecture ; playlist inconnue →
  refus ; pause. **Phase 2b faite le même jour** : bibliothèque NAS +
  favoris Sonos via le websocket HA (`ws_browse` dans `_sonos_common.py` ;
  bibliothèque en cache disque quotidien `data/sonos-library.json`,
  re-scan manuel via le bouton « 🔄 NAS » du dashboard →
  `POST /api/sonos/refresh` ; favoris en mémoire 10 min) — « depuis le
  NAS » force la bibliothèque
  (conteneurs
  artiste/album jouables : « Joue Adele depuis le NAS » joue tout
  l'artiste), NAS et favoris en secours derrière les catalogues, playlists
  = alias → favoris (SQ:n) → playlists iTunes du NAS → Spotify explicite.
  Limite : listing des pistes plafonné ~1000 (titre NAS best effort).
  Leçon ops : modifier un module partagé `plugins/_*.py` exige un restart
  du bot (sys.modules), contrairement aux plugins eux-mêmes. Vérifié en
  vrai (Adele NAS, playlist favorite Chill, par la voix).

- **2026-08-19** — **Sortie du mode privé + fil inversé** (analyse de la
  session invités, voir `docs/DECISIONS.md` 19/08) : bouton « 🔔
  Réveiller » + `POST /api/resume` (lève le hold de toutes les sessions —
  la levée vocale à barre pleine reste inchangée, le bouton est l'issue
  garantie) ; refus de levée désormais journalisés (motif seulement) ;
  fil de conversation du dashboard inversé (dernier message en haut,
  boutons toujours visibles). Constats consignés : latence 24 s d'une
  génération LLM en plein brouhaha (contention GPU probable — à
  instrumenter, voir bancs d'essai) ; 2 faux éveils du canal brut en
  environnement bruyant (données pour l'item « vrai modèle d'éveil »).

- **2026-08-19** — **« Merci. » fantômes (musique dans le micro)** : retour
  de l'hallucination Whisper du 13/08 le premier jour de lecture Sonos
  (33/161 tours, mesuré — voir `docs/DECISIONS.md`). Fix : mot identique
  ×3+ = hallucination filtrée (« Merci. Merci. Merci. ») ; « oui oui »
  doublé reste valide. VAD et barre des clôtures inchangés. Lead parqué :
  gating conscient de la musique (vérification stricte des tours courts
  pendant qu'une enceinte joue) si les fantômes persistent.

- **2026-08-21** — **Notifications : Telegram prioritaire, iMessage en
  secours** (`notify.py`). Cause du changement : les iMessages envoyés
  depuis son propre Apple ID vers soi-même arrivent sans bannière iOS
  (messages « synchronisés ») — les alertes du monitor étaient visibles
  mais silencieuses. `notify.send()` = Telegram (Bot API, urllib stdlib,
  timeout 15 s, best-effort) puis fallback iMessage ; config
  `data/notify.json {"telegram": {"token", "chat_id"}}` ou env
  `MERLIN_NOTIFY_TELEGRAM_TOKEN/CHAT` ; helper `notify.py chat-id`
  (découverte du chat_id via getUpdates). Appelants migrés : `workshop.py`,
  `ops/check-ai-stack.sh`. Tests : `tools/test_notify.py` (10 cas).
  Bot créé et configuré le 21/08 (@merlin_voice_groriri_bot, token +
  chat_id dans `data/notify.json`), test réel « sent », bot relancé.

- **2026-08-21** — **Script d'inscription guidé** (`voice_profile.py enroll`
  imprime `ENROLL_SCRIPT`) : 8 phrases françaises, une condition acoustique
  par phrase (distance, voix douce, intonation, dos au téléphone, pièce
  d'usage). Le gate étant indépendant du texte, le script vise la diversité
  de conditions, pas les mots — pour éviter le profil mono-session (faux
  rejet « marées », voir `docs/DECISIONS.md` 2026-08-14 et 2026-08-21).
  À l'ouverture (neuf et top-up), le script est aussi poussé sur le
  téléphone via `notify.send()` (Telegram, fallback iMessage, best-effort)
  pour être lisible en se déplaçant dans la pièce. Testé réel : « sent ».
  Préparation à l'inscription de la femme de Fred.

- **2026-08-21** — **Hermes retiré** (gateway :8642, router :8101, webui) :
  les événements HA le traversaient et chargeaient le qwen de BASE (262 k),
  évinçant le modèle épinglé de Merlin → flapping du monitor (alertes
  Telegram en rafale) + cold starts. Services arrêtés, LaunchAgents
  supprimés (plists archivés `~/hermes-retired-20260821/`), bot relancé et
  ré-épinglé, 4/4 ok. Incident détaillé dans `docs/DECISIONS.md`.
  Monitor resserré dans la foulée : le check « LLM épinglé » greppe le tag
  exact `-ctx32k` (l'ancien grep `qwen` disait ok avec le mauvais modèle).
  Côté HA : rien à nettoyer dans la config (aucune automatisation ni
  rest_command vers :8642 — c'était Hermes qui s'abonnait au websocket HA,
  abonnement mort avec lui) ; le token longue durée « hermes » a été
  révoqué (seul « Merlin » subsiste), stack 4/4 ok après révocation.

- **2026-08-21** — **Outil `home_assistant`** (item de la revue du 13/08) :
  contrôle de la maison à la voix via le HA Yellow (REST). Périmètre = ce
  que HA expose réellement : lumières (`light`, dont les groupes de pièce
  Hue Cuisine/Chambre/Dressing/Entrée) et scènes d'éclairage (`scene`) —
  **pas de volets ni de thermostat dans HA à ce jour**, à étendre quand les
  entités existeront. Actions : allumer/éteindre (une lumière ou « tout »),
  luminosité (absolue, ±N, plus/moins = ±20), scène, statut. Même gate que
  Sonos : ambiguïté → on n'agit pas, on renvoie les candidats. Le client HA
  générique a été extrait de `_sonos_common.py` vers `plugins/_ha_common.py`
  (la « phase 1 » de `docs/SONOS.md` tenait sa promesse). Tests offline :
  `tools/test_home_assistant.py` (7 cas, fake HA) ; vérifié en réel
  (statut + allumer/éteindre Suspension Dressing). `docs/DECISIONS.md`.

- **2026-08-22** — **Attribution forte, étape 1** : (1) **marge
  d'attribution** (`ATTRIB_MARGIN` 0.05) — nommer un tour exige une avance
  sur le 2e profil, sinon « voix ambiguë » droppée avec les deux scores
  (mesuré : une phrase de camille à marge −0.02 partait chez fred) ; l'ancre
  d'échange désambiguïse les suites, les stops ne sont pas margés ;
  (2) **scoring top-3** (`TOPK_SIMS`) au lieu du centroïde — équivalent
  aujourd'hui (mesuré), robuste quand les profils se diversifient, aucun
  recalibrage de seuil nécessaire ; (3) **capture du jeu d'éval**
  (`tools/eval_capture.py`, wav+txt via le canal de prod, gitignoré) et
  **banc de modèles** (`tools/bench_speaker.py`, 5 candidats en cache).
  Fumée : TitaNet-L EER 0 % / marges +0.40 sur voix de démo (à confirmer
  sur les nôtres — item 2). Tests 13/13. `docs/DECISIONS.md` 2026-08-22.

## À faire (par ordre de valeur estimée)

1. ~~Top-up du profil de Fred en conditions cuisine + musique~~ **fait 21/08
   puis INVERSÉ le 22/08 — fausse bonne idée** : les embeddings musique ont
   rendu le profil poreux (famille acceptée comme fred à 0.61–0.86 toute la
   matinée, emballement de l'adaptation, inscription de Camille volée).
   Profil de Fred réinitialisé + ré-inscrit AU CALME ; gardes d'adaptation
   ajoutées (marge inter-profils, gel pendant inscription, journalisation).
   Verdict : faux rejets en musique = tour perdu assumé ou micro dédié
   (item 13) — plus jamais de top-up en bruit. `docs/DECISIONS.md`
   2026-08-22. (Le bug top-up-au-cap corrigé le 21/08 reste valable.)
2. ~~Changer de modèle d'embedding locuteur~~ **fait 24/08** :
   **CAM++ → TitaNet-L**, tranché sur le banc (`tools/bench_speaker.py`,
   44 énoncés réels : EER 31.8 % → 6.8 %, cross max 0.37 vs self p10 0.54,
   26 ms/énoncé). Seuils recalibrés sur les mesures (0.45 / marges 0.15-0.20 /
   SHORT_WAKE 0.40 — calé au-dessus du cross mesuré, leçon documentée),
   profils reconstruits depuis le jeu d'éval, vérifié bout-en-bout sur audio
   réel (passage de micro fred↔camille sans confusion). Reste : enregistrer
   le FILS (`tools/eval_capture.py start <nom>`) et re-passer le banc ;
   AS-norm seulement si les seuils dérivent à l'usage. `docs/DECISIONS.md`
   2026-08-24.
3. **Inscrire la famille** (action utilisateur) : `tools/voice_profile.py
   enroll <nom>`, puis la personne suit le script imprimé, seule avec
   Merlin. Vérifier le passage de micro (« Merlin, et pour moi… » en
   phrase complète).
4. **Plugin `demande_a_claude` (escalade cloud opt-in)** : outil appelé sur
   demande explicite (« demande à Claude », « réfléchis vraiment ») → modèle
   cloud en streaming, réponse parlée phrase par phrase derrière une
   phrase-pont. Backend en env `MERLIN_ESCALATE_*` (Claude Opus 5 ou Gemini
   3.1 Pro, à A/B sur usage réel). Seuls les tours escaladés quittent la
   machine ; inactif hors activateur Fred / en mode famille. Voir
   `docs/DECISIONS.md` 2026-08-21 (« Merlin plus puissant »).
5. **Exploiter transcripts.db comme jeu de test** : après ~1 semaine d'usage,
   rejouer les lignes `[filtré: …]` et les vraies transcriptions pour ajuster
   les seuils sur données réelles, enrichir `data/stt_vocab.txt` avec les mots
   mal reconnus, et comparer des variantes Whisper (fine-tunes français).
6. **`voice_profile.py prune`** : retirer les embeddings aberrants d'un profil
   (celui à consistance min ~0.47 chez Fred est un candidat).
7. **Inscription par commande vocale** : « Merlin, apprends la voix de Camille »
   → appelle un plugin qui ouvre l'inscription (aujourd'hui : CLI seulement).
8. **Gating de Whisper hors attention** (privacy + compute) : ne transcrire que
   si l'attention est ouverte ou si le moteur d'éveil vient de tirer. À peser :
   on perdrait la collecte de données STT hors attention. (Tension notée avec
   un éventuel Kyutai STT always-on — voir `docs/DECISIONS.md` 2026-08-21.)
9. **Dashboard riche v2 (mi-terme)** : le dashboard vanilla du 17/08 reste le
   client du quotidien ; une app plus ambitieuse (React ou autre, avec build)
   vivra **à côté** (ex. montée sur `/app`), en réutilisant le même contrat :
   token Bearer, REST `/api/workshop*`, messages RTVI du data channel (dont
   `server-message`/`gate-decision`). Idées : historique `transcripts.db`,
   stats du gate par locuteur, replay des tours filtrés, gestion des profils.
10. **Approbation par réponse Telegram** (entrant) : seulement si l'usage des
   notifications sortantes le justifie. Le bot Telegram existe côté sortant
   depuis le 21/08 (`notify.py`) — il manque le long-polling `getUpdates`,
   l'allowlist chat_id et le slug explicite dans la réponse (jamais un
   « oui » nu). L'option chat.db iMessage (Full Disk Access, schéma fragile)
   est abandonnée. Voir `docs/DECISIONS.md` 2026-08-16 et 2026-08-21.
11. **Entraîner un vrai modèle d'éveil** (openWakeWord custom « Merlin » sur
   données synthétiques françaises) si le zipformer montre des faiblesses en
   conditions bruyantes.
12. **Sonos multiroom** : plan complet en 4 phases dans **`docs/SONOS.md`**
   (architecture arrêtée le 18/08, voir `docs/DECISIONS.md`). Résumé : HA
   Yellow = plan de contrôle (REST), lecture toujours native Sonos (liens de
   partage), résolveurs minces (Music.app AppleScript pour Apple Music perso,
   iTunes Search pour le catalogue, alias/Web API Spotify, index NAS Sonos),
   Music.app→AirPlay 2 seulement pour les playlists perso. La phase 1 crée le
   client HA réutilisable par l'outil `home_assistant` (lumières/volets, item
   de la revue du 13/08).
13. **« Mode débat » speech-to-speech (lead parqué)** : session opt-in sur un
    modèle audio-natif cloud (GPT-Realtime-2 ou Gemini Live — pipecat 1.3.0
    embarque les deux services) en pipeline parallèle : le gate local ouvre
    la session, « Merlin stop » (canal brut, local) la tue. Écarté comme
    boucle principale (audio continu vers le cloud = rupture voice_guard).
    Exige sa propre décision privacy dans `docs/DECISIONS.md` avant tout
    code. Voir `docs/DECISIONS.md` 2026-08-21.
14. **Micro dédié cuisine (lead PROMU le 22/08)** : la voie « diversité de
    profil en bruit » est morte (item 1, incident du 22/08) — le micro
    dédié est désormais LE levier restant contre les faux rejets en
    musique. Un micro FIXE aide doublement (SNR loin
    champ + canal acoustique constant → profil stable), mais aucun AEC ne
    soustraira la musique Sonos (source externe, pas de signal de
    référence) et le plafond « parole simultanée » demeure. Candidat déjà
    possédé : le **HA Voice Preview Edition** (`home_assistant_voice_098c6a`
    dans HA, débranché à ce jour — front-end XMOS loin champ). Intégration
    non triviale : aujourd'hui il parlerait à l'Assist de HA (servi par les
    `com.wyoming.*`) en CONTOURNANT Merlin — il faudrait un ingest audio
    Wyoming/satellite dans bot.py pour que voice_guard garde l'audio brut.
    Nouveau micro = nouveau canal → top-up du profil sur ce micro requis.

## À faire — reliquat de la revue du 13/08 (« The Merlin Review »)

Vérifié le 14/08 : ces points de la revue sont toujours ouverts.

### Ops

- ~~Épingler les dépendances~~ **fait 15/08** (versions du venv, procédure de
  montée de version en commentaire du fichier).
- ~~Cold start Ollama~~ **fait 15/08** (`_preload_llm()` dans bot.py,
  `keep_alive:-1` via l'endpoint natif au démarrage).
- ~~Contexte 262 K~~ **fait 15/08** : tag `-ctx32k` (num_ctx 32768), −5 Go,
  TTFT identique (`tools/bench_longctx.py`).
- ~~Contexte de session non borné~~ **fait 15/08** : `HistoryTrimmer` coupe à
  système + `MERLIN_MAX_HISTORY_MSGS` (40) sans orphaner de résultat d'outil.
- ~~Endpoint ouvert~~ **fait 17/08** : token partagé Bearer sur `/api/offer`
  et `/api/workshop*` (`dashboard_api.py`, `data/auth-token`). Tailscale
  reste possible par-dessus pour l'accès hors LAN.
- **Vrai certificat** (Tailscale serve / mkcert) pour tuer l'avertissement
  du téléphone.
- ~~LaunchAgents périmés~~ **fait 18/08** : warmup supprimé, monitor réécrit,
  `com.merlin.bot` (KeepAlive) pour la survie au reboot — voir `ops/README.md`.
  ~~Sort de `com.wyoming.*`~~ **tranché 19/08 : CONSERVER** — le Yellow a
  des entités Wyoming actives (`stt.faster_whisper`, `stt.mlx_whisper`,
  `tts.piper`, `tts.piper_2` via `integration_entities('wyoming')`), ces
  LaunchAgents servent l'assist vocal de HA.
- **Issues Pipecat upstream** à déposer (keep-alive coupe l'audio ; pacing en
  rafale — reproduisibles avec `tools/probe_barge_in.py` ; RTVI `send-text` :
  le restore de skip_tts tombe entre les deux runs LLM d'un tour à outil,
  la réponse « silencieuse » est parlée — voir `docs/DECISIONS.md` 17/08).

### Fonctionnalités

- **Profil nocturne** (moitié manquante de la mémoire « plan A ») : job qui
  distille `transcripts.db` en `profile.md` lisible/éditable, injecté au
  démarrage de session. Ensuite seulement : rappel par embeddings (plan B,
  `nomic-embed-text` + table keyée sur turns.id) ; mem0 uniquement si A+B
  plafonnent.
- ~~Outil `home_assistant`~~ **fait 21/08** (lumières + scènes via HA Yellow
  REST ; pas de volets — aucune entité `cover` dans HA, à étendre le jour où
  il y en aura). Voir « Fait » et `docs/DECISIONS.md` 2026-08-21.
- **`delegate()`** (ex-« → Hermes », retiré le 21/08) : tâches longues hors
  chemin chaud via un worker headless (agy/codex, comme l'atelier), résultat
  en follow-up parlé ou briefing matinal.
- **Raisonnement à la demande** : outil qui relance le même modèle avec
  `reasoning_effort` élevé (4–5 s, annoncé par une phrase-pont).

### Bancs d'essai (données avant conviction — utiliser transcripts.db)

- **Latence en conversation chargée (constat du 19/08)** : une génération
  LLM de 24 s pendant un brouhaha continu (STT/éveil/embeddings sur le
  même GPU que qwen, + interruption d'agrégation juste avant), et ~35 s
  sur le premier tour après un restart du bot (vu 2×). Instrumenter le
  TTFT par tour (métriques pipecat) avant tout tuning.

- **LLM, étape 0 — FAIT 14/08, verdict : rester sur q4_K_M.** nvfp4/MLX
  mesuré : TTFT médian 0,09 s vs 0,33 s mais régression tool-call (9/15 vs
  14/15 à temp 0,2, un refus déterministe). Détails dans `docs/DECISIONS.md`.
  Re-tester à la prochaine release Ollama ; variante `-mtp` jamais essayée.
- ~~Fixer `temperature` ≈ 0,2 dans bot.py~~ **fait 15/08** (`LLM_TEMPERATURE`,
  défaut 0,2). Reste : vérifier à l'oreille que le ton ne devient pas
  monotone ; sinon monter à 0,3–0,4.
- ~~LLM, A/B~~ **clos 15/08 : qwen3.6:35b-a3b confirmé titulaire** après
  deux vagues de bench (7 challengers) et un A/B en production perdu par
  mistral-small3.2 sur la latence à contexte long (prefill dense). Tout est
  dans `docs/DECISIONS.md`, y compris la leçon de protocole (bench vocal =
  inclure un tour à ~10 k chars de contexte). Restent sur disque avec un
  rôle : nemotron (candidat worker delegate), muse-glimmer nvfp4-dflash
  (piste caméra), qwen3.6-nvfp4 (retest prochaine release Ollama). À
  surveiller : un MoE de la génération qwen3.8.
- **STT** : jeu de test personnel de ~20 énoncés depuis transcripts.db, puis
  Parakeet v3 (parakeet-mlx) ; **Kyutai STT (`stt-1b-en_fr`, MLX) = upgrade
  stratégique** (transcription streaming 0,5 s de délai, VAD sémantique,
  timestamps mots) mais **bloqué derrière l'instrumentation TTFT** (un 1B
  qui transcrit en continu sur le GPU du qwen épinglé = le scénario des
  24 s du 19/08) ; service Pipecat custom à écrire ; Unmute écarté
  (orchestration = Pipecat, CUDA-only) ; unification éveil/STT = lead
  séparé, pas dans la même bascule. Voir `docs/DECISIONS.md` 2026-08-21.
- **TTS** : audition **Kyutai Pocket TTS d'abord** (100 M params, temps réel
  sur CPU — soulage le GPU —, français, clonage : une voix propre à Merlin ;
  voir `docs/DECISIONS.md` 2026-08-21), puis Chatterbox Multilingual v3
  (clonage, MIT — vérifier le real-time factor sur MPS) ; Kyutai TTS 1.6B si
  Kyutai STT ; Kokoro reste le fallback derrière une env var.

## Plafond connu

- **Parole simultanée** : deux voix en même temps → embedding mélangé, le tour
  est rejeté (échec sûr). Seule une vraie diarisation lèverait ça — lourd,
  pas prévu.
