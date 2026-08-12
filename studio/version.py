"""Which code is actually running — the first question of every support turn.

An operator on another machine runs a command and pastes the output; without
a version stamp there is no way to tell a real failure from a stale checkout,
and the answer to 'did you pull?' is always 'I think so'. So every operator-
facing entry point prints this line.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    try:
        out = subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def code_version() -> str:
    """A short human-readable stamp: commit, date, and whether the working
    tree carries uncommitted edits."""
    commit = _git("rev-parse", "--short", "HEAD")
    if not commit:
        return "unknown (not a git checkout)"
    when = _git("log", "-1", "--format=%cd", "--date=short")
    dirty = " +local edits" if _git("status", "--porcelain") else ""
    return f"{commit}{f' ({when})' if when else ''}{dirty}"


def behind_by() -> int:
    """How many commits origin/main is ahead of this checkout, or 0 when
    level/unknown. Counted from the last fetch — no network here."""
    count = _git("rev-list", "--count", "HEAD..origin/main")
    try:
        return int(count)
    except ValueError:
        return 0


def banner() -> str:
    """The line an operator can paste back: version, plus a nudge when the
    checkout is known to be behind."""
    line = f"code: {code_version()}"
    behind = behind_by()
    if behind:
        line += (f"  ⚠ {behind} commit{'s' if behind > 1 else ''} behind "
                 "origin/main — run: git pull origin main")
    return line
