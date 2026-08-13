"""Promote a workshop candidate to a live plugin: the human approval step.

Usage: venv/bin/python tools/approve_skill.py <slug>

Moves candidates/<slug>.py to plugins/<slug>.py (after you have read it!),
keeps the test alongside the others in tools/, commits, and reminds you to
restart the bot.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    slug = sys.argv[1]
    candidate = REPO / "candidates" / f"{slug}.py"
    test = REPO / "candidates" / f"test_{slug}.py"
    if not candidate.exists():
        print(f"no candidate at {candidate}")
        sys.exit(1)
    target = REPO / "plugins" / f"{slug}.py"
    test_target = REPO / "tools" / f"test_{slug}.py"
    candidate.rename(target)
    if test.exists():
        text = test.read_text(encoding="utf-8").replace(f"candidates/{slug}.py", f"plugins/{slug}.py")
        test_target.write_text(text, encoding="utf-8")
        test.unlink()
    subprocess.run(["git", "add", "-A"], cwd=REPO, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"approve skill '{slug}': promote candidate to plugins/"],
                   cwd=REPO, check=True)
    print(f"approved: {target.relative_to(REPO)}")
    print("restart the bot to load it: ./venv/bin/python bot.py")


if __name__ == "__main__":
    main()
