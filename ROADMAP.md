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

## Plafond connu

- **Parole simultanée** : deux voix en même temps → embedding mélangé, le tour
  est rejeté (échec sûr). Seule une vraie diarisation lèverait ça — lourd,
  pas prévu.
