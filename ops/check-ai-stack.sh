#!/bin/bash
# Merlin stack health check — current stack: ollama serve (Ollama.app) + bot.py :7860.
# Run every 5 min by com.merlin.monitor (ops/launchd/); on ok<->fail transition
# notifies via notify.py — Telegram puis iMessage (best-effort, silent if data/notify.json absent).
# Manual run: bash ~/scripts/check-ai-stack.sh (wrapper to this file).
REPO="/Users/fred/Developer/ai/merlin-voice"
STATE="/tmp/merlin-monitor.state"
PASS=0; FAIL=0; DETAIL=""
# PAS de ((PASS++)) : statut 1 quand PASS vaut 0, ce qui déclencherait le || fail.
ok()   { echo "ok $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL $1"; DETAIL+="$1, "; FAIL=$((FAIL+1)); }

curl -sf --max-time 5 http://localhost:11434/api/version >/dev/null \
  && ok "Ollama :11434" || fail "Ollama :11434"
# The LLM must stay pinned in memory (_preload_llm, keep_alive:-1 — cold start
# fix du 2026-08-15) : un /api/ps vide = 6-7 s de rechargement au premier tour.
# Tag EXACT (= LLM_MODEL de bot.py) : un grep "qwen" trop lâche répondait ok
# quand seul le tag de base 262k était chargé (incident Hermes du 2026-08-21).
PINNED_TAG="qwen3.6:35b-a3b-q4_K_M-ctx32k"
pinned() { curl -sf --max-time 5 http://localhost:11434/api/ps | grep -q "\"$PINNED_TAG\""; }
# Auto-réparation (2026-10-21) : l'auto-update d'Ollama.app redémarre le
# serveur et décharge le modèle, or bot.py n'épingle qu'à son démarrage. On
# ré-épingle avec le même appel que _preload_llm (endpoint NATIF : l'OpenAI-compat
# traduit mal keep_alive -1), puis on re-vérifie. Alerte seulement si ça échoue.
if pinned; then
  ok "LLM épinglé ($PINNED_TAG)"
elif curl -sf --max-time 120 http://localhost:11434/api/generate \
       -d "{\"model\":\"$PINNED_TAG\",\"keep_alive\":-1}" >/dev/null && pinned; then
  echo "$(date '+%F %T') LLM absent de /api/ps -> ré-épinglé"
  ok "LLM ré-épinglé ($PINNED_TAG)"
else
  fail "LLM épinglé"
fi
curl -skf --max-time 5 https://localhost:7860/ >/dev/null \
  && ok "Bot :7860" || fail "Bot :7860"
# Home Assistant (plan de contrôle Sonos — docs/SONOS.md). Vérifié seulement
# si le token existe : avant ça, HA ne fait pas partie du stack surveillé.
HA_TOKEN_FILE="$REPO/data/ha-token"
if [[ -f "$HA_TOKEN_FILE" ]]; then
  HA_URL="${MERLIN_HA_URL:-$(cat "$REPO/data/ha-url" 2>/dev/null)}"
  curl -sf --max-time 5 -H "Authorization: Bearer $(cat "$HA_TOKEN_FILE")" \
    "${HA_URL:-http://homeassistant.local:8123}/api/" >/dev/null \
    && ok "Home Assistant" || fail "Home Assistant"
fi

echo ""
echo "Result: $PASS ok, $FAIL failed"

# Notification uniquement sur transition d'état (jamais toutes les 5 min).
NOW=$([[ $FAIL -eq 0 ]] && echo ok || echo fail)
PREV=$(cat "$STATE" 2>/dev/null || echo ok)
echo "$NOW" > "$STATE"
if [[ "$NOW" != "$PREV" ]]; then
  if [[ "$NOW" == fail ]]; then MSG="⚠️ Merlin en panne : ${DETAIL%, }"
  else MSG="✅ Merlin rétabli ($PASS ok)"; fi
  (cd "$REPO" && venv/bin/python -c "import notify,sys; notify.send(sys.argv[1])" "$MSG") \
    >/dev/null 2>&1
fi
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
