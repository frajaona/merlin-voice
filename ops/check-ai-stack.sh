#!/bin/bash
# Merlin stack health check — current stack: ollama serve (Ollama.app) + bot.py :7860.
# Run every 5 min by com.merlin.monitor (ops/launchd/); on ok<->fail transition
# sends an iMessage via notify.py (best-effort, silent if data/notify.json absent).
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
curl -sf --max-time 5 http://localhost:11434/api/ps | grep -q "qwen" \
  && ok "LLM épinglé (qwen)" || fail "LLM épinglé"
curl -skf --max-time 5 https://localhost:7860/ >/dev/null \
  && ok "Bot :7860" || fail "Bot :7860"

echo ""
echo "Result: $PASS ok, $FAIL failed"

# iMessage uniquement sur transition d'état (jamais toutes les 5 min).
NOW=$([[ $FAIL -eq 0 ]] && echo ok || echo fail)
PREV=$(cat "$STATE" 2>/dev/null || echo ok)
echo "$NOW" > "$STATE"
if [[ "$NOW" != "$PREV" ]]; then
  if [[ "$NOW" == fail ]]; then MSG="⚠️ Merlin en panne : ${DETAIL%, }"
  else MSG="✅ Merlin rétabli ($PASS ok)"; fi
  (cd "$REPO" && venv/bin/python -c "import notify,sys; notify.send_imessage(sys.argv[1])" "$MSG") \
    >/dev/null 2>&1
fi
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
