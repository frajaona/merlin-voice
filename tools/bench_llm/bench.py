"""Bench LLM vocal : latence (court + ~10 k chars d'historique) + discipline outils.

Backends :
  ollama:<tag>   endpoint OpenAI d'Ollama (temp 0,2, reasoning_effort none)
  bonsai:<pack>  Ternary Bonsai 2.5 en process via le loader MLX du pack

Mesures par requête : TTFT (premier token de contenu OU d'appel d'outil),
débit de décodage, texte, appels d'outils.
Usage : python bench.py <backend> <ctx.json> <out.json>
  ollama : venv/bin/python -I tools/bench_llm/bench.py ollama:<tag> ctx.json out.json
  bonsai : venv séparé (requirements du pack : mlx 0.32, mlx-vlm 0.6.3).
ctx.json = {"system": <prompt FR du bot>, "tools": [schémas OpenAI des plugins]}
(généré le 2026-11-25 depuis lang_profile.FR + plugins/*.SCHEMA ; voir
docs/DECISIONS.md 2026-11-25). Vérifier `/api/health` (connections 0) avant :
le bench partage le GPU avec le bot.
"""
import json
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from cases import LATENCY, TOOL_CASES  # noqa: E402

RUNS = 3
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


def history(target_chars=10_000):
    msgs, total, i = [], 0, 0
    while total < target_chars:
        u, a = _FILLER_PAIRS[i % len(_FILLER_PAIRS)]
        msgs += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        total += len(u) + len(a)
        i += 1
    return msgs


# ---------------------------------------------------------------- backends
class Ollama:
    URL = "http://127.0.0.1:11434/v1/chat/completions"

    def __init__(self, tag):
        self.tag = tag

    def run(self, messages, tools, max_tokens=200):
        body = {"model": self.tag, "stream": True, "messages": messages,
                "temperature": 0.2, "reasoning_effort": "none",
                "max_tokens": max_tokens, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(self.URL, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer x"})
        t0 = time.monotonic()
        ttft, text, calls, ntok = None, [], {}, 0
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                obj = json.loads(payload)
                if obj.get("usage"):
                    ntok = obj["usage"].get("completion_tokens", ntok)
                if not obj.get("choices"):
                    continue
                d = obj["choices"][0].get("delta", {})
                if d.get("content") or d.get("tool_calls"):
                    if ttft is None:
                        ttft = time.monotonic() - t0
                if d.get("content"):
                    text.append(d["content"])
                for tc in d.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                    f = tc.get("function", {})
                    c["name"] += f.get("name") or ""
                    c["arguments"] += f.get("arguments") or ""
        total = time.monotonic() - t0
        out = []
        for c in calls.values():
            try:
                args = json.loads(c["arguments"] or "{}")
            except Exception:
                args = {"_raw": c["arguments"]}
            out.append({"name": c["name"], "arguments": args})
        return {"ttft": ttft, "total": total, "text": "".join(text),
                "tool_calls": out, "ntok": ntok}


class Bonsai:
    def __init__(self, pack):
        from pathlib import Path
        sys.path.insert(0, str(Path(pack) / "runtime"))
        import mlx.core as mx
        from vision_artifact import load_vl_model
        self.mx = mx
        t = time.monotonic()
        self.model, self.processor, self.config = load_vl_model(Path(pack))
        print(f"[bonsai] chargé en {time.monotonic() - t:.1f}s", flush=True)
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        self.tpl = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(
            (Path(pack) / "chat_template.jinja").read_text())

    def render(self, messages, tools):
        return self.tpl.render(messages=messages, tools=tools or None,
                               add_generation_prompt=True, enable_thinking=False)

    snapshot = None      # (prefix_ids, cache) — système + outils, comme le cache d'Ollama
    use_prefix_cache = True

    def prepare_prefix(self, base, tools):
        """base = messages communs (système [+ historique]) ; le tour suivant varie."""
        from prefix_cache import build_snapshot, common_prefix
        base = base if isinstance(base, list) else [base]
        enc = self.processor.tokenizer.encode
        a = enc(self.render(base + [{"role": "user", "content": "a"}], tools), add_special_tokens=False)
        b = enc(self.render(base + [{"role": "user", "content": "zz b"}], tools), add_special_tokens=False)
        ids = common_prefix(a, b)
        t = time.monotonic()
        self.snapshot = (ids, build_snapshot(self.model, ids))
        print(f"[bonsai] préfixe {len(ids)} tokens pré-rempli en {time.monotonic() - t:.2f}s", flush=True)

    def run(self, messages, tools, max_tokens=200, temperature=0.2):
        from mlx_vlm import stream_generate
        from mlx_vlm.generate.dispatch import PromptCacheState
        from prefix_cache import clone_cache
        prompt = self.render(messages, tools)
        kw = {}
        if self.use_prefix_cache and self.snapshot:
            st = PromptCacheState()
            st.token_ids = list(self.snapshot[0])
            st.cache = clone_cache(self.snapshot[1])   # hors chrono : fait à l'avance en prod
            kw["prompt_cache_state"] = st
        t0 = time.monotonic()
        ttft, pieces, ntok = None, [], 0
        # Mêmes réglages que bot.py (temp 0,2) ; top_p/top_k de generation_config.
        for r in stream_generate(self.model, self.processor, prompt,
                                 max_tokens=max_tokens, temperature=temperature,
                                 top_p=0.95, top_k=20, **kw):
            if ttft is None:
                ttft = time.monotonic() - t0
            pieces.append(r.text)
            ntok += 1
        total = time.monotonic() - t0
        raw = "".join(pieces)
        text, calls = parse_qwen_tools(raw)
        return {"ttft": ttft, "total": total, "text": text, "tool_calls": calls,
                "ntok": ntok, "raw": raw,
                "peak_gb": self.mx.get_peak_memory() / 1e9}


def parse_qwen_tools(raw):
    """Formats Qwen : <tool_call>{json}</tool_call> ou XML <function=..><parameter=..>."""
    import re
    calls = []
    for blk in re.findall(r"<tool_call>(.*?)(?:</tool_call>|$)", raw, re.S):
        blk = blk.strip()
        try:
            j = json.loads(blk)
            calls.append({"name": j.get("name", ""), "arguments": j.get("arguments", {})})
            continue
        except Exception:
            pass
        m = re.search(r"<function=([^>\s]+)>", blk)
        if m:
            params = dict(re.findall(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", blk, re.S))
            calls.append({"name": m.group(1), "arguments": params})
        else:
            calls.append({"name": "?", "arguments": {"_raw": blk}})
    text = re.sub(r"<tool_call>.*?(</tool_call>|$)", "", raw, flags=re.S).strip()
    return text, calls


# ---------------------------------------------------------------- bench
def main():
    backend, ctx_path, out_path = sys.argv[1:4]
    ctx = json.load(open(ctx_path))
    kind, arg = backend.split(":", 1)
    be = Ollama(arg) if kind == "ollama" else Bonsai(arg)
    sysmsg = {"role": "system", "content": ctx["system"]}
    tools = ctx["tools"]
    res = {"backend": backend, "latency": [], "long": [], "tools": []}

    if kind == "bonsai":
        be.prepare_prefix(sysmsg, tools)  # = cache de préfixe d'Ollama
    be.run([sysmsg, {"role": "user", "content": "Bonjour"}], tools)  # warmup

    print("== latence court (prompt + outils réels du bot) ==", flush=True)
    for q in LATENCY:
        for _ in range(RUNS):
            r = be.run([sysmsg, {"role": "user", "content": q}], tools)
            r["q"] = q
            res["latency"].append(r)
        print(f"  TTFT {r['ttft']:.2f}s  {r['ntok']} tok/{r['total']:.2f}s  "
              f"outils={[c['name'] for c in r['tool_calls']]}  « {r['text'][:90]} »", flush=True)

    print("== contexte long (~10 k chars) ==", flush=True)
    longm = [sysmsg] + history() + [{"role": "user", "content": "Résume-moi ce qu'on s'est dit en une phrase."}]
    if kind == "bonsai":
        # 1er passage froid (historique jamais vu), puis historique en cache
        # comme en prod où le tour précédent est déjà dans le KV.
        be.use_prefix_cache = False
        r = be.run(longm, tools); res["long_cold"] = r
        print(f"  [froid] TTFT {r['ttft']:.2f}s total {r['total']:.2f}s", flush=True)
        be.use_prefix_cache = True
        be.prepare_prefix(longm[:-1], tools)
    for _ in range(RUNS):
        r = be.run(longm, tools)
        res["long"].append(r)
        print(f"  TTFT {r['ttft']:.2f}s total {r['total']:.2f}s « {r['text'][:90]} »", flush=True)

    print("== outils (18 cas) ==", flush=True)
    if kind == "bonsai":
        be.prepare_prefix(sysmsg, tools)
    for q, ok_tools, need_news in TOOL_CASES:
        r = be.run([sysmsg, {"role": "user", "content": q}], tools)
        names = [c["name"] for c in r["tool_calls"]]
        hit = bool(set(names) & ok_tools)
        news_ok = (not need_news) or any(
            c["name"] == "web_search" and c["arguments"].get("type") == "news" for c in r["tool_calls"])
        r.update(q=q, hit=hit, news_ok=news_ok)
        res["tools"].append(r)
        flag = "OK " if hit and news_ok else ("news?" if hit else "RATÉ")
        print(f"  [{flag}] {q}  → {r['tool_calls'] or r['text'][:100]}", flush=True)

    lat = [r["ttft"] for r in res["latency"] if r["ttft"]]
    dec = [r["ntok"] / max(r["total"] - r["ttft"], 1e-3) for r in res["latency"] if r["ttft"] and r["ntok"] > 5]
    res["summary"] = {
        "ttft_median_court": statistics.median(lat),
        "ttft_median_long": statistics.median(r["ttft"] for r in res["long"]),
        "decode_tok_s_median": statistics.median(dec) if dec else None,
        "tools_hit": sum(r["hit"] for r in res["tools"]),
        "tools_strict": sum(r["hit"] and r["news_ok"] for r in res["tools"]),
        "spurious_tool_on_latency": sum(bool(r["tool_calls"]) for r in res["latency"]),
    }
    if kind == "bonsai":
        res["summary"]["peak_gb"] = be.mx.get_peak_memory() / 1e9
    print(json.dumps(res["summary"], indent=1), flush=True)
    json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
