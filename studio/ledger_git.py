"""Carrying an approval decision back to the repository it came from.

The draft queue is a git ledger precisely because two machines are involved:
the cloud drafts, the operator releases. That only holds if the decision
travels back — otherwise the next cycle sees a draft it already asked about,
the operator's pull starts conflicting, and the queue slowly stops meaning
anything. Leaving that to a remembered `git add && git commit && git push`
after every approval is how it stops happening.

So resolving a draft commits and pushes the ledger itself. Rules:

  · scoped to data/drafts — the operator's other work in the tree is theirs
  · best effort — a decision already taken must never be lost to a failed
    network call; the caller is TOLD what happened and can push by hand
  · a rejected push is retried once after a rebase pull, because the cloud
    writing new drafts while the operator approves is the normal case
  · STUDIO_LEDGER_AUTOPUSH=0 turns it off for anyone who wants the manual
    workflow back
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = "data/drafts"


def enabled() -> bool:
    return os.environ.get("STUDIO_LEDGER_AUTOPUSH", "1").strip() not in {
        "0", "false", "no", "off"}


def _git(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        out = subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                             text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr).strip()
    except Exception as e:  # git missing, permissions, timeout
        return 1, str(e)[:200]


def _branch() -> str:
    code, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 and out and out != "HEAD" else "main"


def publish_decision(draft_id: str, status: str) -> str:
    """Commit and push the ledger. Returns a short human-readable outcome —
    empty when there was nothing to do or the feature is off."""
    if not enabled():
        return ""
    if not (ROOT / ".git").exists():
        return ""

    code, out = _git("status", "--porcelain", "--", LEDGER)
    if code != 0:
        return f"could not read git status ({out[:80]})"
    if not out:
        return ""                      # already committed, nothing to carry

    code, out = _git("add", "--", LEDGER)
    if code != 0:
        return f"could not stage the ledger ({out[:80]})"

    code, out = _git("commit", "-m", f"drafts: {status} {draft_id}")
    if code != 0:
        return f"could not commit the decision ({out[:80]})"

    branch = _branch()
    code, out = _git("push", "origin", branch, timeout=120)
    if code == 0:
        return f"committed and pushed to {branch}"

    # the usual cause: the cloud pushed new drafts since the last pull
    code_pull, _ = _git("pull", "--rebase", "origin", branch, timeout=120)
    if code_pull == 0:
        code, out = _git("push", "origin", branch, timeout=120)
        if code == 0:
            return f"committed and pushed to {branch} (after a rebase pull)"
    return ("committed locally, but the push failed — run "
            f"`git push origin {branch}` when the network is back "
            f"({out.splitlines()[-1][:80] if out else 'no detail'})")
