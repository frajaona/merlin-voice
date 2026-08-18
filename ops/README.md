# Ops — launchd (survie au reboot)

Source de vérité des agents launchd de Merlin. Les plists installés dans
`~/Library/LaunchAgents/` sont des **copies** de `ops/launchd/` — modifier ici,
puis réinstaller.

## Agents

- **`com.merlin.bot`** — lance `venv/bin/python bot.py` au login, le relance
  s'il meurt (`KeepAlive`). Redémarrage :
  `launchctl kickstart -k gui/$(id -u)/com.merlin.bot`
  (remplace l'ancien kill-par-port ; un simple `kill` du process marche aussi,
  launchd le relance). Reprendre la main manuellement (dev) :
  `launchctl bootout gui/$(id -u)/com.merlin.bot`, puis `venv/bin/python bot.py`.
  stdout/stderr : `/tmp/merlin-bot.log` (les vrais logs restent `data/merlin.log`).
- **`com.merlin.monitor`** — `ops/check-ai-stack.sh` toutes les 5 min :
  Ollama :11434, modèle qwen épinglé (`/api/ps`), bot :7860. iMessage via
  `notify.py` sur transition ok↔fail uniquement. Log : `/tmp/merlin-monitor.log`.
- `ollama serve` n'a pas d'agent à nous : Ollama.app gère son propre démarrage.
- `~/scripts/check-ai-stack.sh` est un wrapper vers `ops/check-ai-stack.sh`.

## (Ré)installation

```sh
cp ops/launchd/com.merlin.*.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.merlin.bot 2>/dev/null
launchctl bootout gui/$(id -u)/com.merlin.monitor 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.merlin.bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.merlin.monitor.plist
```

## Historique

- 2026-08-18 : `com.merlin.warmup` supprimé (curl d'un router :8101 mort ;
  remplacé par `_preload_llm()` keep_alive:-1 dans bot.py). `com.merlin.monitor`
  réécrit (il surveillait Honcho/Router/Wyoming/docker — l'ancienne stack).
  `com.wyoming.whisper`/`com.wyoming.piper` laissés en place : possiblement
  utilisés par Home Assistant (protocole Wyoming) — à confirmer avant suppression.
