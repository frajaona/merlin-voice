"""Bench TTFT court + contexte long (~10k chars) sur l'endpoint OpenAI d'Ollama.

Leçon de protocole (docs/DECISIONS.md, A/B qwen vs mistral) : un bench vocal
doit inclure un tour à ~10 k chars d'historique — le TTFT mono-tour à contexte
court ne prédit pas la latence en fin de session.

Usage: venv/bin/python tools/bench_longctx.py <model> [<model2> ...]
"""
import json
import statistics
import sys
import time
import urllib.request

URL = "http://127.0.0.1:11434/v1/chat/completions"
RUNS = 3

SYSTEM_PROMPT = """Tu es Merlin, un assistant personnel intelligent et chaleureux. Tu réponds toujours en français.
Règles importantes :
- Tes réponses seront lues à voix haute — pas de markdown, pas de symboles spéciaux.
- Phrases courtes et naturelles. Maximum deux phrases par réponse sauf si on te demande des détails."""

# ~10k chars d'historique plausible (assistant vocal, tours courts).
_FILLER_PAIRS = [
    ("Quelle heure est-il à Tokyo quand il est midi à Paris ?",
     "Quand il est midi à Paris, il est vingt heures à Tokyo. Le Japon a sept ou huit heures d'avance selon la saison."),
    ("Rappelle-moi ce qu'on mange ce soir déjà ?",
     "Ce soir tu avais prévu un gratin de courgettes avec du riz. Tu m'avais dit que les courgettes étaient dans le bac du bas."),
    ("Et il faut le mettre au four combien de temps ?",
     "Environ trente-cinq minutes à cent quatre-vingts degrés. Tu peux vérifier la cuisson en piquant les courgettes avec un couteau."),
    ("Mets un minuteur de trente-cinq minutes alors.",
     "C'est fait, minuteur de trente-cinq minutes lancé. Je te préviendrai quand ce sera prêt."),
    ("Est-ce qu'il va pleuvoir demain matin ?",
     "Demain matin le ciel sera couvert mais sans pluie avant midi. L'après-midi en revanche, des averses sont attendues vers quinze heures."),
    ("Du coup je peux aller courir à quelle heure ?",
     "Le meilleur créneau est entre huit heures et onze heures, avant les averses. Il fera environ dix-sept degrés, parfait pour courir."),
]


def _history(target_chars: int):
    msgs = []
    total = 0
    i = 0
    while total < target_chars:
        u, a = _FILLER_PAIRS[i % len(_FILLER_PAIRS)]
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
        total += len(u) + len(a)
        i += 1
    return msgs


def run_once(model, messages, timeout=300):
    # Mêmes réglages que bot.py : temp 0,2 et raisonnement coupé — sans
    # reasoning_effort none le modèle pense avant de parler et le TTFT ment.
    body = json.dumps({"model": model, "stream": True, "messages": messages,
                       "temperature": 0.2, "reasoning_effort": "none"}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer x"})
    t0 = time.monotonic()
    ttft = None
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
            if delta.get("content"):
                if ttft is None:
                    ttft = time.monotonic() - t0
                text.append(delta["content"])
    return ttft, time.monotonic() - t0, "".join(text)


def bench(model, label, messages):
    ttfts, totals = [], []
    for i in range(RUNS):
        ttft, total, text = run_once(model, messages)
        ttfts.append(ttft)
        totals.append(total)
        print(f"  [{label} #{i+1}] TTFT={ttft:.2f}s total={total:.2f}s"
              f"  reply: {text[:80].replace(chr(10), ' ')}")
        sys.stdout.flush()
    print(f"  [{label}] TTFT médian {statistics.median(ttfts):.2f}s"
          f"  total médian {statistics.median(totals):.2f}s")


if __name__ == "__main__":
    models = sys.argv[1:] or ["qwen3.6:35b-a3b-q4_K_M"]
    short = [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": "Quelle est la capitale de l'Australie ?"}]
    hist = _history(10_000)
    long_ctx = ([{"role": "system", "content": SYSTEM_PROMPT}] + hist +
                [{"role": "user", "content": "Résume-moi ce qu'on s'est dit en une phrase."}])
    n_chars = sum(len(m["content"]) for m in long_ctx)
    for model in models:
        print(f"\n=== {model} ===")
        run_once(model, short)  # warmup (charge le modèle si froid)
        print(" court (système + question) :")
        bench(model, "court", short)
        print(f" long ({n_chars} chars d'historique) :")
        bench(model, "long", long_ctx)
