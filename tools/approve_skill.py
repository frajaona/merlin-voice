"""Promote a workshop candidate to a live plugin: the human approval step.

Usage: venv/bin/python tools/approve_skill.py <slug>

Read candidates/<slug>.py first! Then this moves it to plugins/, moves the
test to tools/, and commits. Running sessions pick it up on the next
conversation (plugins are rescanned per connection).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skill_admin import promote


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    slug = sys.argv[1]
    try:
        target = promote(slug)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)
    print(f"approved: {target}")
    print("active for every new conversation (no restart needed).")


if __name__ == "__main__":
    main()
