"""Skill workshop: turns pending feature requests into plugin candidates.

Reads data/feature-requests.jsonl, hands the oldest pending request to a
headless coding CLI (worker chain: agy first, codex as fallback), gates the
result (AST safety scan + generated smoke test), and leaves an inert candidate
in candidates/ committed to git. Nothing is ever activated automatically:
promotion to plugins/ goes through tools/approve_skill.py.

Run manually, from cron, or spawned by the request_feature plugin.
"""
import ast
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
REQUESTS_FILE = REPO / "data" / "feature-requests.jsonl"
READY_FILE = REPO / "data" / "skill-ready.jsonl"
LOG_FILE = REPO / "data" / "workshop.log"
CANDIDATES = REPO / "candidates"
VENV_PYTHON = REPO / "venv" / "bin" / "python"

WORKERS = os.getenv("WORKSHOP_WORKERS", "agy,codex").split(",")
AGY_MODEL = os.getenv("AGY_MODEL", "gemini-3.1-pro-high")
WORKER_TIMEOUT_SECS = int(os.getenv("WORKSHOP_TIMEOUT_SECS", "900"))

# Modules a generated plugin may never import. Coarse by design: a candidate
# that legitimately needs one of these deserves a human-written plugin instead.
BANNED_IMPORTS = {"subprocess", "ctypes", "pty", "pickle", "multiprocessing", "shutil", "socket"}


def log(msg: str):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_requests() -> list:
    if not REQUESTS_FILE.exists():
        return []
    return [json.loads(l) for l in REQUESTS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]


def save_requests(requests: list):
    REQUESTS_FILE.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests), encoding="utf-8"
    )


_SLUG_STOPWORDS = {"le", "la", "les", "un", "une", "de", "des", "du", "d", "l",
                   "en", "et", "a", "au", "aux", "pour", "avec", "sur", "dans", "que"}


def slugify(capability: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", capability.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", text).split() if w not in _SLUG_STOPWORDS]
    return "_".join(words[:4]) or "skill"


def build_prompt(slug: str, request: dict) -> str:
    return f"""Tu travailles dans le dépôt de Merlin, un assistant vocal français (Pipecat).

Lis d'abord plugins/README.md (le contrat des outils) et plugins/web_search.py (un exemple complet).

Crée exactement deux fichiers, et ne modifie AUCUN autre fichier :

1. candidates/{slug}.py — un plugin implémentant la capacité : « {request.get('capability')} »
   (demande originale de l'utilisateur : « {request.get('user_request') or request.get('capability')} »).
   Il doit respecter strictement le contrat de plugins/README.md (exports SCHEMA et handler,
   result_callback exactement une fois, jamais de blocage de l'event loop, descriptions en français).
   N'importe jamais ces modules : {", ".join(sorted(BANNED_IMPORTS))}.
   Le fichier sera déplacé tel quel dans plugins/ après revue humaine — les imports doivent
   fonctionner depuis n'importe quel emplacement du dépôt.

2. candidates/test_{slug}.py — un test autonome exécutable avec `venv/bin/python candidates/test_{slug}.py`
   depuis la racine du dépôt. Il doit : importer le plugin depuis candidates/{slug}.py via importlib,
   vérifier SCHEMA (FunctionSchema, nom = "{slug}") et que handler est une coroutine, puis appeler
   handler avec un faux FunctionCallParams (arguments réalistes, result_callback et llm.push_frame mockés)
   et vérifier que result_callback est appelé exactement une fois avec un dict. Exit code 0 si tout passe.

Ne fais aucun commit git. Quand les deux fichiers sont écrits et que le test passe, termine."""


def run_worker(worker: str, prompt: str) -> bool:
    worker = worker.strip()
    if worker == "agy":
        cmd = ["agy", "-p", prompt, "--dangerously-skip-permissions",
               "--model", AGY_MODEL, "--print-timeout", f"{WORKER_TIMEOUT_SECS}s"]
    elif worker == "codex":
        cmd = ["codex", "exec", "--full-auto", prompt]
    else:
        log(f"unknown worker '{worker}', skipping")
        return False
    log(f"worker {worker} starting")
    try:
        result = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                                timeout=WORKER_TIMEOUT_SECS)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"worker {worker} failed to run: {e}")
        return False
    tail = (result.stdout or result.stderr or "").strip()[-400:]
    log(f"worker {worker} exited rc={result.returncode}; tail: {tail}")
    return result.returncode == 0


def gate_ast(plugin_path: Path) -> str | None:
    """Static safety scan. Returns an error string, or None if clean."""
    try:
        tree = ast.parse(plugin_path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return f"syntax error: {e}"
    has_schema = has_handler = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root in BANNED_IMPORTS:
                    return f"banned import: {root}"
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and (node.attr.startswith("exec") or node.attr in ("system", "popen", "remove", "rmdir", "unlink")):
                return f"banned call: os.{node.attr}"
        if isinstance(node, ast.Assign):
            has_schema = has_schema or any(getattr(t, "id", None) == "SCHEMA" for t in node.targets)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handler":
            has_handler = True
    if not has_schema:
        return "missing SCHEMA export"
    if not has_handler:
        return "missing async handler export"
    return None


def gate_test(test_path: Path) -> str | None:
    """Run the generated smoke test in a subprocess. None if it passes."""
    try:
        result = subprocess.run([str(VENV_PYTHON), str(test_path)], cwd=REPO,
                                capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "smoke test timed out"
    if result.returncode != 0:
        return f"smoke test failed: {(result.stderr or result.stdout).strip()[-300:]}"
    return None


def commit_candidate(slug: str) -> bool:
    try:
        subprocess.run(["git", "add", f"candidates/{slug}.py", f"candidates/test_{slug}.py"],
                       cwd=REPO, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m",
                        f"workshop: candidate skill '{slug}' (awaiting human approval)\n\n"
                        f"Generated by the skill workshop; inert until promoted to plugins/\n"
                        f"with tools/approve_skill.py."],
                       cwd=REPO, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        log(f"git commit failed: {e.stderr.decode(errors='replace')[-200:] if e.stderr else e}")
        return False


def process_one() -> bool:
    requests = load_requests()
    pending = next((r for r in requests if r.get("status") == "pending"), None)
    if pending is None:
        log("no pending request")
        return False
    slug = slugify(pending.get("capability", ""))
    plugin_path = CANDIDATES / f"{slug}.py"
    test_path = CANDIDATES / f"test_{slug}.py"
    log(f"processing request: [{pending.get('capability')}] -> {slug}")
    CANDIDATES.mkdir(exist_ok=True)
    pending["status"] = "building"
    pending["building_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_requests(requests)

    outcome = None
    for worker in WORKERS:
        for stale in (plugin_path, test_path):
            stale.unlink(missing_ok=True)
        if not run_worker(worker, build_prompt(slug, pending)):
            continue
        if not plugin_path.exists() or not test_path.exists():
            log(f"worker {worker} finished but candidate files are missing")
            continue
        error = gate_ast(plugin_path) or gate_test(test_path)
        if error:
            log(f"worker {worker} candidate rejected: {error}")
            continue
        outcome = worker
        break

    if outcome is None:
        pending["status"] = "failed"
        pending["failed_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_requests(requests)
        log(f"all workers failed for '{slug}'")
        return False

    committed = commit_candidate(slug)
    pending["status"] = "built"
    pending["slug"] = slug
    pending["worker"] = outcome
    pending["built_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_requests(requests)
    with open(READY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "slug": slug,
            "capability": pending.get("capability", ""),
            "worker": outcome,
            "ts": pending["built_ts"],
            "announced": False,
        }, ensure_ascii=False) + "\n")
    log(f"candidate '{slug}' built by {outcome} (committed={committed}); "
        f"approve with: venv/bin/python tools/approve_skill.py {slug}")
    return True


if __name__ == "__main__":
    process_one()
