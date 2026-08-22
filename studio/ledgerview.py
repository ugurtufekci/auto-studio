"""Production history from the git ledger — the numbers every machine agrees on.

Cycles run in the cloud and leave their evidence in git: one report per run
under reports/, one JSON record per draft under data/drafts. The local
SQLite store only knows cycles run on THIS machine, and the Pipeline screen
read that store — so a studio that had run nineteen cloud cycles and pushed
thirty-odd drafts showed "no cycles" over a row of six zeros. The operator
said it three days before a CEO demo: "o kadar işlem yaptık, pipeline hep
boş gözüküyor." What production has done must be read from what production
committed."""

from __future__ import annotations

import json
import re
from pathlib import Path

from studio import draftpool

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"


def _outcome_line(text: str) -> str:
    """The report's own one-line verdict. Every cycle report bolds one
    sentence near the top ("Mixed outcome: June drafted (2); Mara FAILED
    again on TTS."); short bold fragments are field labels, not verdicts."""
    for m in re.finditer(r"\*\*(.+?)\*\*", text[:1500], re.S):
        line = " ".join(m.group(1).split())
        if len(line) > 20:
            return line[:220]
    return ""


def cycle_runs(limit: int = 14) -> list[dict]:
    """Newest first. `file` is the report's basename for the /report
    endpoint; `ok` is False when the report's own verdict says FAILED."""
    if not REPORTS_DIR.exists():
        return []
    runs = []
    for p in sorted(REPORTS_DIR.glob("cycle-*.md"), reverse=True)[:limit]:
        m = re.match(r"cycle-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})$", p.stem)
        when = f"{m.group(1)} {m.group(2)}:{m.group(3)}" if m else p.stem
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            text = ""
        outcome = _outcome_line(text)
        runs.append({"when": when, "file": p.name, "outcome": outcome,
                     "ok": "FAILED" not in outcome.upper()})
    return runs


def _drafts():
    for folder in (draftpool.PENDING_DIR, draftpool.RESOLVED_DIR):
        if not folder.exists():
            continue
        for p in folder.glob("*.json"):
            try:
                yield json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue


def totals() -> dict:
    """What the whole operation has produced, counted from the ledger."""
    t = {"cycles": len(list(REPORTS_DIR.glob("cycle-*.md")))
                   if REPORTS_DIR.exists() else 0,
         "drafts": 0, "published": 0, "rejected": 0, "waiting": 0,
         "media": 0}
    personas: set[str] = set()
    for d in _drafts():
        t["drafts"] += 1
        t["media"] += len(d.get("media_files")
                          or ([d["media_file"]] if d.get("media_file") else []))
        status = d.get("status", "")
        if status in draftpool.SUCCESS_STATUSES:
            t["published"] += 1
        elif status == draftpool.REJECTED:
            t["rejected"] += 1
        elif status == "pending":
            t["waiting"] += 1
        if d.get("persona"):
            personas.add(d["persona"])
    t["personas"] = len(personas)
    return t


def report_path(name: str) -> Path | None:
    """Resolve a report by BASENAME only — no directories, no traversal."""
    if not re.fullmatch(r"cycle-[\w-]+\.md", name):
        return None
    p = REPORTS_DIR / name
    return p if p.exists() else None
