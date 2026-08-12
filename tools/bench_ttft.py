"""Benchmark TTFT (time to first token) and total latency:
Hermes gateway vs direct Ollama, with the Merlin voice system prompt."""
import json
import re
import sys
import time
import urllib.request

# Read Hermes key from bot.py fallback (no .env present)
src = open("/Users/fred/Developer/ai/merlin-voice/bot.py").read()
m = re.search(r'HERMES_API_KEY = os\.getenv\("HERMES_API_KEY", "([^"]+)"\)', src)
HERMES_KEY = m.group(1) if m else ""

SYSTEM_PROMPT = """Tu es Merlin, un assistant personnel intelligent et chaleureux. Tu réponds toujours en français.
Règles importantes :
- Tes réponses seront lues à voix haute — pas de markdown, pas de symboles spéciaux.
- Phrases courtes et naturelles. Maximum deux phrases par réponse sauf si on te demande des détails."""

QUESTIONS = [
    "Bonjour Merlin, explique-moi en deux phrases pourquoi le ciel est bleu.",
    "Quelle est la capitale de l'Australie ?",
]

TARGETS = [
    ("hermes (default model)", "http://127.0.0.1:8642/v1/chat/completions", HERMES_KEY, "default"),
    ("ollama qwen3.5:9b-mlx", "http://127.0.0.1:11434/v1/chat/completions", "x", "qwen3.5:9b-mlx"),
    ("ollama qwen3.6:35b-a3b", "http://127.0.0.1:11434/v1/chat/completions", "x", "qwen3.6:35b-a3b-q4_K_M"),
]


def run_once(url, key, model, question, timeout=180):
    body = json.dumps({
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    t0 = time.monotonic()
    ttft = None
    tt_any = None
    n_chunks = 0
    n_reasoning = 0
    text = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except Exception:
                continue
            reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
            content = delta.get("content") or ""
            if (reasoning or content) and tt_any is None:
                tt_any = time.monotonic() - t0
            if reasoning:
                n_reasoning += 1
            if content:
                if ttft is None:
                    ttft = time.monotonic() - t0
                n_chunks += 1
                text.append(content)
    total = time.monotonic() - t0
    return ttft, tt_any, total, n_chunks, n_reasoning, "".join(text)


for name, url, key, model in TARGETS:
    print(f"\n=== {name} ===")
    for i, q in enumerate(QUESTIONS):
        label = "cold" if i == 0 else "warm"
        try:
            ttft, tt_any, total, n, n_r, text = run_once(url, key, model, q)
            preview = text[:120].replace("\n", " ")
            print(f"  [{label}] TTF-content={ttft:.2f}s TTF-any={tt_any:.2f}s total={total:.2f}s content_chunks={n} reasoning_chunks={n_r}")
            print(f"         reply: {preview}")
        except Exception as e:
            print(f"  [{label}] ERROR: {e}")
        sys.stdout.flush()
