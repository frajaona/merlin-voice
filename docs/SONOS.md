# Sonos — plan d'implémentation

Contrôle vocal des enceintes Sonos du foyer (transport, volume, groupes) et
lancement de musique (Apple Music **principal**, Spotify — surtout les
enfants —, bibliothèque perso sur le NAS). Décision d'architecture et
alternatives écartées : voir `docs/DECISIONS.md` 2026-08-18.

## Architecture retenue (résumé)

- **Plan de contrôle : Home Assistant (Yellow)**, via son API REST + token
  longue durée. Toutes les commandes = appels de service `media_player.*`.
  L'intégration Sonos de HA (bâtie sur SoCo) découvre les enceintes et tient
  la topologie des groupes.
- **Lecture : TOUJOURS native Sonos** (comptes Apple Music / Spotify liés
  dans l'app Sonos, index NAS de Sonos). Merlin ne fait que *résoudre* la
  demande vocale en un lien de partage / URI, puis le donne à l'enceinte.
  Les enceintes streament seules — rien ne dépend du Mac ni du téléphone.
- **Trois résolveurs minces** (pas de service tiers) :
  - *Apple Music catalogue* : iTunes Search API (gratuite, sans auth) →
    lien de partage.
  - *Apple Music perso* (playlists, albums ajoutés) : bibliothèque de
    Music.app sur le Mac, interrogée en AppleScript (iCloud Music Library =
    copie locale, toujours à jour, zéro credential).
  - *Spotify* : alias `data/sonos-aliases.json` pour les playlists des
    enfants (petit jeu stable) ; Web API client-credentials pour le
    catalogue.
  - *NAS* : index bibliothèque de Sonos lui-même (limite 65 k pistes).
- **Music.app en AirPlay 2 = chemin de lecture SECONDAIRE uniquement**, pour
  les playlists Apple Music perso (pas de lien de partage public). Tout le
  reste passe par la lecture native.

## Phase 0 — Prérequis (setup, pas de code Merlin)

> **Statut 19/08 : fait** (verdicts dans `docs/DECISIONS.md` 2026-08-19),
> sauf le toggle macOS « Réseau local » pour « Python » (action Fred).
> Verdict du point 3 : `play_media` relaie Apple Music ET Spotify —
> pas de fallback SoCo. URL HA épinglée par IP dans `data/ha-url`.

Actions one-shot + vérifications qui conditionnent la suite :

1. HA Yellow : vérifier que l'intégration Sonos voit toutes les enceintes ;
   créer un token longue durée → `data/ha-token` (même pattern que
   `data/auth-token`) + env `MERLIN_HA_URL`.
2. App Sonos : comptes Apple Music et Spotify liés ; partage NAS indexé
   (compter les pistes vs la limite 65 k).
3. **Point à vérifier (décisif pour la phase 2)** : est-ce que
   `media_player.play_media` de HA accepte les liens de partage Apple
   Music/Spotify sur Sonos ? Sinon, fallback confiné : appel SoCo
   `ShareLinkPlugin` en direct depuis le plugin (HA garde tout le reste).
4. Mac : autorisation Automation (TCC) pour `osascript` → Music.app depuis
   le contexte launchd ; inventaire des noms d'appareils AirPlay vs noms de
   pièces Sonos.
5. Monitoring : ajouter un ping `GET /api/` de HA dans
   `ops/check-ai-stack.sh` (nouvelle dépendance du stack).
6. Au passage : trancher le sort de `com.wyoming.*` (item ROADMAP — le
   Yellow les utilise-t-il ?).

## Phase 1 — Contrôle : plugin `sonos_controle`

Le socle. Un plugin (contrat `plugins/README.md`), client HA REST minimal
(httpx async, token Bearer), **écrit main — jamais un candidat atelier**
(réseau + subprocess bannis par le scan AST).

- Commandes : play/pause/reprendre/suivant/précédent/stop, shuffle on/off,
  repeat off/all/one, volume absolu/relatif/mute, **grouper/dégrouper** des
  pièces, « qu'est-ce qui joue ? » (état + artiste/titre/pièce).
- Résolution des pièces par nom flou en français (« la cuisine », « le
  salon ») ; pièce par défaut configurable (env).
- Tests hors-ligne avec un fake HA (même pattern duck-typé que
  `test_dashboard_api.py`) ; round-trip réel via `tools/probe_tool_call.py`.

**Critère de sortie** : « Merlin, mets pause dans la cuisine », « monte le
son du salon », « groupe la cuisine avec le salon » fonctionnent à la voix.

## Phase 2 — Musique : plugin `sonos_musique` (résolveurs → lecture native)

> **Statut 19/08 : fait, avec périmètre ajusté** (voir `docs/DECISIONS.md`
> 2026-08-19 phase 2) : résolveurs v1 = alias → iTunes Search → Spotify
> (credentials requis, `data/spotify-app.json`). **NAS reporté en 2b** (le
> REST de HA ne sait pas parcourir la bibliothèque Sonos — il faudra le
> websocket ou SoCo). **Résolveur Music.app déplacé en phase 3** avec sa
> lecture (les playlists perso ne sont pas jouables avant, et ça évite un
> second consentement TCC). Playlists = alias uniquement (ne pas
> improviser) ; « joue <artiste> » = son album le plus en vue, annoncé.
> Alias : `data/sonos-aliases.json`, format `{"nom parlé": "https://…"}`.
> Validé bout-en-bout en vrai (Discovery dans la Cuisine par la voix).
>
> **Phase 2b faite le même jour** : bibliothèque NAS **et favoris Sonos**
> via le websocket HA (`_sonos_common.ws_browse`). Cache bibliothèque :
> **disque, quotidien** (`data/sonos-library.json`, `MERLIN_SONOS_LIBRARY_TTL`),
> re-scan **manuel uniquement** — bouton « 🔄 NAS » du dashboard
> (`POST /api/sonos/refresh`) ; favoris : mémoire 10 min.
> « depuis le NAS » (service=nas) force la bibliothèque ; sinon NAS puis
> favoris passent en secours derrière les catalogues. Playlists : alias →
> favoris Sonos (SQ:n) → playlists iTunes du NAS → Spotify explicite.
> Artistes/albums NAS jouables en conteneurs. Limite connue : le listing
> des pistes plafonne à ~1000 (recherche par titre = best effort).
> Validé en vrai (« Joue Adele depuis le NAS », « la playlist Chill »).

La valeur principale : « joue X dans la pièce Y ».

- Chaîne : demande vocale → résolveur → lien de partage / URI → lecture
  native Sonos (via HA `play_media`, ou fallback SoCo ShareLink selon le
  verdict de la phase 0).
- Résolveurs, dans l'ordre : alias (`data/sonos-aliases.json`) →
  bibliothèque Music.app (AppleScript, `asyncio.to_thread`) → iTunes Search
  (Apple Music catalogue) → Spotify Web API (client credentials) → index
  NAS Sonos. Favoris Sonos jouables par nom aussi (filet de sécurité).
- Ambiguïté : principe du gate — **préférer ne pas agir** ; en dessous d'un
  score de correspondance franc, demander confirmation plutôt que jouer le
  mauvais album.
- Playlists perso Apple Music : à ce stade, résolues (on sait dire « je la
  connais ») mais lecture = phase 3 ; interim possible via favoris Sonos.
- Filler parlé obligatoire (« Je lance ça. ») — résolution > 1 s probable.

**Critère de sortie** : « mets le dernier album de Daft Punk dans le
salon », « mets la playlist des enfants dans leur chambre » (alias Spotify),
« joue [artiste du NAS] » fonctionnent.

## Phase 3 — Playlists perso Apple Music : Music.app → AirPlay 2

Le seul contenu sans lien de partage public. Music.app (Mac, 24/7 sous
launchd) est scriptable jusqu'à la sélection des appareils AirPlay.

- AppleScript : jouer une playlist de la bibliothèque, cibler une ou
  plusieurs enceintes Sonos en AirPlay 2, volume par appareil.
- **Routage par source** : mémoriser quel chemin a lancé la lecture
  (native vs AirPlay) ; `sonos_controle` route pause/suivant/état vers HA
  ou vers Music.app selon le cas. Règle simple, un seul état partagé.
- Limites assumées : session liée au Mac ; l'état « qu'est-ce qui joue »
  vit dans Music.app, pas dans Sonos.

**Critère de sortie** : « mets ma playlist jogging dans la salle de bain »
fonctionne, et « pause » marche quel que soit le chemin qui a lancé.

## Phase 4 — Transferts et confort (plus tard)

- **Spotify OAuth utilisateur** (one-shot) : énumération live des playlists
  (les alias deviennent optionnels) + **Spotify Connect transfer**
  (`PUT /me/player`) : « continue sur la cuisine » / « remets ça sur mon
  iPhone », position préservée — le seul vrai handoff qui existe.
- **Sonos → iPhone (Apple Music)** : Merlin connaît le morceau via HA →
  iMessage avec le lien `music.apple.com` (réutilise `notify.py`). Une
  tape, ça reprend sur le téléphone (début de piste). Approximation
  assumée : Apple n'offre aucun transfert de session vers/depuis des
  enceintes tierces — plafond documenté, pas un bug.
- **Politique voix enfants** (décision explicite à consigner) : les outils
  Sonos accessibles aux voix enfants (inscription ou `MERLIN_FAMILY_MODE`),
  avec garde-fous possibles : plafond de volume sur requête attribuée à un
  enfant, défaut vers l'espace « playlists enfants ».

## Écarté (voir DECISIONS.md pour le détail)

- **Music Assistant** : différé indéfiniment. Se rouvre seulement si :
  limite 65 k du NAS atteinte, besoin de son transfer_queue inter-pièces,
  ou envie de métadonnées riches. Son provider Apple Music
  (reverse-engineered) ne doit jamais porter le service principal.
- **SoCo comme chemin primaire** : HA existe déjà ; deux chemins vers les
  mêmes enceintes = taxe de maintenance. SoCo ne survit que comme fallback
  ShareLink confiné si HA ne relaie pas les liens.
- **node-sonos-http-api** : démon Node qui duplique une lib Python.
- **API cloud Sonos** : aller-retour cloud pour « mets pause » — contraire
  au local-first.
- **MusicKit** : rendu inutile par la bibliothèque Music.app (même donnée,
  zéro token, zéro abonnement développeur).
