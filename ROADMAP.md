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
4. **Attribution du locuteur dans les transcriptions** : stocker « qui a parlé »
   (l'info existe déjà dans le gate) — utile pour le résumé nocturne.
5. **Inscription par commande vocale** : « Merlin, apprends la voix de Camille »
   → appelle un plugin qui ouvre l'inscription (aujourd'hui : CLI seulement).
6. **Gating de Whisper hors attention** (privacy + compute) : ne transcrire que
   si l'attention est ouverte ou si le moteur d'éveil vient de tirer. À peser :
   on perdrait la collecte de données STT hors attention.
7. **Entraîner un vrai modèle d'éveil** (openWakeWord custom « Merlin » sur
   données synthétiques françaises) si le zipformer montre des faiblesses en
   conditions bruyantes.

## À faire — reliquat de la revue du 13/08 (« The Merlin Review »)

Vérifié le 14/08 : ces points de la revue sont toujours ouverts.

### Ops

- **Épingler les dépendances** : `requirements.txt` est sans versions (sauf
  pipecat ≥1.3.0) alors que bot.py monkeypatche des internals Pipecat — un
  `pip install -U` peut casser la voix en silence.
- **Cold start Ollama** : aucun `OLLAMA_KEEP_ALIVE` (vérifié : modèle déchargé
  après idle) → 10–20 s de reload sur la première question. `-1` ou
  `keep_alive` par requête.
- **Contexte 262 K** : le KV cache du 35B est dimensionné pour 262 144 tokens
  (~28 Go résidents pour un fichier de 18 Go). 16–32 K libèrent ~8–10 Go ;
  re-vérifier le TTFT avec `tools/bench_ttft.py`.
- **Contexte de session non borné** : la liste de messages grandit sans limite
  dans une connexion — résumer ou tronquer aux N derniers tours.
- **Endpoint ouvert** : `/api/offer` sans aucune auth — n'importe qui sur le
  LAN utilise le GPU. Un token partagé, ou Tailscale.
- **Vrai certificat** (Tailscale serve / mkcert) pour tuer l'avertissement
  du téléphone.
- **LaunchAgents périmés** : `com.merlin.monitor` + `com.merlin.warmup` et
  `~/scripts/check-ai-stack.sh` surveillent l'ANCIENNE stack (Honcho, Router
  :8101, Wyoming Whisper/Piper, docker) — à réécrire pour la stack actuelle
  (ollama serve + bot.py, survie au reboot) ou à supprimer.
- **Issues Pipecat upstream** à déposer (keep-alive coupe l'audio ; pacing en
  rafale) — reproduisibles avec `tools/probe_barge_in.py`.

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
- **Fixer `temperature` ≈ 0,2 dans bot.py** (mesuré 14/08 : à temp 1 — le
  défaut actuel — le modèle saute web_search ~40 % du temps sur l'actualité ;
  à 0,2 : 14/15, zéro appel parasite). Vérifier à l'oreille que le ton ne
  devient pas monotone ; sinon 0,3–0,4.
- **LLM, A/B — bench fait 15/08, finale : qwen vs mistral-small3.2.**
  Nemotron 3.5 (invente la météo, vouvoie), Muse Glimmer (TTFT 2,4–5 s) et
  Gemma 4 26B (thinking non désactivable, outils 10/18) éliminés — détails
  dans `docs/DECISIONS.md`. Reste à faire : une semaine d'usage réel avec
  `LLM_MODEL=mistral-small3.2` (18/18 outils, TTFT 0,27 s, français très
  naturel), juger le ton à l'oreille. Nemotron à retenir comme candidat
  worker de fond (delegate), Muse Glimmer pour une future caméra. Seconde
  vague 15/08 (variantes MLX, qwen3.8:27b, gemma4:12b-mlx) : la finale ne
  change pas — détails dans `docs/DECISIONS.md`.
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
