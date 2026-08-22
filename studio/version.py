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


# The ledger writes data commits all day (every approve, reject and cycle
# advances HEAD), so "HEAD moved" is NOT "the code changed" — comparing
# code_version() strings told the operator to restart a console that was
# already current, right after every approve. Only these paths hold code
# the running console could be stale against.
_CODE_PATHS = ("dashboard", "studio", "run.py", "config")


def code_fingerprint() -> str:
    """Identity of the CODE on disk, blind to data-only commits: the git
    tree ids of the code paths plus any uncommitted edits under them. Two
    equal fingerprints mean a restart would serve identical behaviour."""
    import hashlib
    trees = _git("ls-tree", "HEAD", "--", *_CODE_PATHS)
    if not trees:
        return "unknown"
    edits = _git("diff", "HEAD", "--", *_CODE_PATHS)
    return hashlib.sha1(f"{trees}\n{edits}".encode("utf-8", "replace")).hexdigest()[:12]


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
