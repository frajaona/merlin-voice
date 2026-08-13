"""Shared logic for promoting workshop candidates to live plugins.

Used by tools/approve_skill.py (manual CLI approval) and by the
approve_skill plugin (voice approval of gate-passed candidates).
"""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent
READY_FILE = REPO / "data" / "skill-ready.jsonl"


def built_entry(slug: str) -> dict | None:
    """Return the skill-ready entry for slug, i.e. proof it passed the gates."""
    if not READY_FILE.exists():
        return None
    for line in READY_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("slug") == slug:
            return entry
    return None


def promote(slug: str) -> Path:
    """Move a candidate into plugins/ (+ its test into tools/) and commit.

    Raises FileNotFoundError / CalledProcessError on failure.
    Returns the new plugin path.
    """
    candidate = REPO / "candidates" / f"{slug}.py"
    test = REPO / "candidates" / f"test_{slug}.py"
    target = REPO / "plugins" / f"{slug}.py"
    if target.exists():
        return target  # already promoted
    if not candidate.exists():
        raise FileNotFoundError(f"no candidate at {candidate}")
    candidate.rename(target)
    if test.exists():
        text = test.read_text(encoding="utf-8").replace(
            f"candidates/{slug}.py", f"plugins/{slug}.py"
        ).replace('Path(__file__).parent / "', 'Path(__file__).parent.parent / "plugins" / "')
        (REPO / "tools" / f"test_{slug}.py").write_text(text, encoding="utf-8")
        test.unlink()
    subprocess.run(["git", "add", "-A"], cwd=REPO, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"approve skill '{slug}': promote candidate to plugins/"],
        cwd=REPO, check=True, capture_output=True,
    )
    return target
