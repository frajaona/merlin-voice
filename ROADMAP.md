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
  refus ; pause. **Reporté en 2b : bibliothèque NAS** (browse Sonos
  impossible en REST HA — passer par le websocket HA ou SoCo).

## À faire (par ordre de valeur estimée)

1. **Inscrire la famille** (action utilisateur) : `tools/voice_profile.py
   enroll <nom>`, puis la personne discute seule avec Merlin. Vérifier le
   passage de micro (« Merlin, et pour moi… » en phrase complète).
2. **Exploiter transcripts.db comme jeu de test** : après ~1 semaine d'usage,
   rejouer les lignes `[filtré: …]` et les vraies transcriptions pour ajuster
   les seuils sur données réelles, enrichir `data/stt_vocab.txt` avec les mots
   mal reconnus, et comparer des variantes Whisper (fine-tunes français).
3. **`voice_profile.py prune`** : retirer les embeddings aberrants d'un profil
   (celui à consistance min ~0.47 chez Fred est un candidat).
4. **Inscription par commande vocale** : « Merlin, apprends la voix de Camille »
   → appelle un plugin qui ouvre l'inscription (aujourd'hui : CLI seulement).
5. **Gating de Whisper hors attention** (privacy + compute) : ne transcrire que
   si l'attention est ouverte ou si le moteur d'éveil vient de tirer. À peser :
   on perdrait la collecte de données STT hors attention.
6. **Dashboard riche v2 (mi-terme)** : le dashboard vanilla du 17/08 reste le
   client du quotidien ; une app plus ambitieuse (React ou autre, avec build)
   vivra **à côté** (ex. montée sur `/app`), en réutilisant le même contrat :
   token Bearer, REST `/api/workshop*`, messages RTVI du data channel (dont
   `server-message`/`gate-decision`). Idées : historique `transcripts.db`,
   stats du gate par locuteur, replay des tours filtrés, gestion des profils.
7. **Approbation par réponse iMessage** (entrant) : seulement si l'usage des
   notifications sortantes le justifie — polling de `chat.db` (Full Disk
   Access, schéma fragile) avec vérification du handle expéditeur + slug
   explicite dans la réponse ; alternative robuste : bot Telegram
   (long-polling, boutons). Voir `docs/DECISIONS.md` 2026-08-16.
8. **Entraîner un vrai modèle d'éveil** (openWakeWord custom « Merlin » sur
   données synthétiques françaises) si le zipformer montre des faiblesses en
   conditions bruyantes.
9. **Sonos multiroom** : plan complet en 4 phases dans **`docs/SONOS.md`**
   (architecture arrêtée le 18/08, voir `docs/DECISIONS.md`). Résumé : HA
   Yellow = plan de contrôle (REST), lecture toujours native Sonos (liens de
   partage), résolveurs minces (Music.app AppleScript pour Apple Music perso,
   iTunes Search pour le catalogue, alias/Web API Spotify, index NAS Sonos),
   Music.app→AirPlay 2 seulement pour les playlists perso. La phase 1 crée le
   client HA réutilisable par l'outil `home_assistant` (lumières/volets, item
   de la revue du 13/08).

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
- **Outil `home_assistant`** (lumières/volets via HA Yellow REST/WebSocket) —
  l'upgrade quotidien le plus visible ; le prompt n'en parle plus, l'outil
  rendrait la promesse réelle.
- **`delegate()` → Hermes** : tâches longues hors chemin chaud, résultat en
  follow-up parlé ou briefing matinal.
- **Raisonnement à la demande** : outil qui relance le même modèle avec
  `reasoning_effort` élevé (4–5 s, annoncé par une phrase-pont).

### Bancs d'essai (données avant conviction — utiliser transcripts.db)

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
  Parakeet v3 (parakeet-mlx) ; Kyutai STT (streaming fr) = upgrade stratégique
  (barge-in instantané), à faire avec son TTS.
- **TTS** : audition Chatterbox Multilingual v3 (clonage, MIT — vérifier le
  real-time factor sur MPS) ; Kyutai TTS si Kyutai STT ; Kokoro reste le
  fallback derrière une env var.

## Plafond connu

- **Parole simultanée** : deux voix en même temps → embedding mélangé, le tour
  est rejeté (échec sûr). Seule une vraie diarisation lèverait ça — lourd,
  pas prévu.
