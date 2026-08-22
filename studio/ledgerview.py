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


def _media_files(d: dict) -> list[str]:
    return d.get("media_files") or ([d["media_file"]] if d.get("media_file") else [])


def published(limit: int = 20) -> list[dict]:
    """Everything that actually went out, newest first — the Content
    screen's cross-machine truth. The URL rides in the resolution note when
    the release (or the hand-post flow) recorded one."""
    rows = []
    for d in _drafts():
        if d.get("status") not in draftpool.SUCCESS_STATUSES:
            continue
        note = str(d.get("note") or "")
        media = [str(draftpool.MEDIA_DIR / f) for f in _media_files(d)]
        cover = draftpool.MEDIA_DIR / (d.get("cover_file") or "cover-that-never-exists")
        rows.append({
            "id": d.get("id", ""), "when": d.get("resolved_at", ""),
            "persona": d.get("persona", ""), "platform": d.get("platform", ""),
            "kind": d.get("media_kind", ""), "text": d.get("text", ""),
            "status": d.get("status", ""),
            "url": note if note.startswith("http") else "",
            "media": [m for m in media if Path(m).exists()][:10],
            "cover": str(cover) if cover.exists() else "",
        })
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


def media_gallery(limit: int = 36) -> list[dict]:
    """Recent rendered files straight from the ledger's media directory,
    newest drafts first — what the Assets screen shows on every machine."""
    out = []
    drafts = sorted(_drafts(), key=lambda x: x.get("created_at", ""),
                    reverse=True)
    for d in drafts:
        cover = draftpool.MEDIA_DIR / (d.get("cover_file") or "cover-that-never-exists")
        for f in _media_files(d):
            p = draftpool.MEDIA_DIR / f
            if not p.exists():
                continue
            kind = "video" if p.suffix.lower() in (".mp4", ".mov", ".webm") \
                else "image"
            out.append({"path": str(p), "kind": kind,
                        "status": d.get("status", ""),
                        "poster": str(cover) if kind == "video" and cover.exists() else "",
                        "label": f"{d.get('persona', '')} · "
                                 f"{d.get('created_at', '')[:10]} · "
                                 f"{d.get('status', '')}"})
            if len(out) >= limit:
                return out
    return out


def published_today(platform: str) -> tuple[int, str | None]:
    """(successes resolved today UTC, latest success timestamp) for one
    platform, from the ledger — a machine-independent floor under the
    machine-local posts table."""
    from datetime import UTC, datetime
    today = datetime.now(UTC).date().isoformat()
    stamps = [d.get("resolved_at") or "" for d in _drafts()
              if d.get("platform") == platform
              and d.get("status") in draftpool.SUCCESS_STATUSES]
    stamps = [s for s in stamps if s]
    return (sum(1 for s in stamps if s[:10] == today),
            max(stamps) if stamps else None)


def recent_success_texts(limit: int = 30) -> list[str]:
    """Captions of the most recent successes across the fleet — what the
    caption-dedupe gate must compare against. The posts table it used to
    read is machine-local, and the drafting machine is a fresh clone every
    day, so the gate had nothing to compare against in production."""
    rows = [(d.get("resolved_at") or "", d.get("text") or "")
            for d in _drafts()
            if d.get("status") in draftpool.SUCCESS_STATUSES and d.get("text")]
    rows.sort(reverse=True)
    return [t for _, t in rows[:limit]]
