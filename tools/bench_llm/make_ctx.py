"""Écrit ctx.json pour bench.py : prompt système FR réel + schémas des plugins.

N'appelle PAS bot._system_prompt (sa note « skill prête » a un effet de bord
sur data/skill-ready.jsonl).
Usage : venv/bin/python tools/bench_llm/make_ctx.py <out.json>
"""
import datetime
import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import lang_profile as lp  # noqa: E402

p = lp.FR
prompt = p.system_prompt.format(current_date=p.date_fn(datetime.date.today()))
tools = []
for f in sorted((ROOT / "plugins").glob("*.py")):
    if f.stem.startswith("_"):
        continue
    s = importlib.import_module(f"plugins.{f.stem}").SCHEMA
    tools.append({"type": "function", "function": {
        "name": s.name, "description": s.description,
        "parameters": {"type": "object", "properties": s.properties, "required": s.required}}})
json.dump({"system": prompt, "tools": tools}, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print(f"{len(prompt)} chars de prompt, outils : {[t['function']['name'] for t in tools]}")
