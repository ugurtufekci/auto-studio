"""The draft queue as a git ledger — drafts travel the way signals do.

The approval flow has two machines in it by design: the cycle can run
anywhere (the daily cloud routine, a laptop), but releasing a draft happens
wherever the operator's console runs. A SQLite row cannot cross that gap —
containers are reclaimed, laptops change. So a finished draft is exported
here, committed by whoever ran the cycle, and pulled by whoever approves:

    data/drafts/pending/<id>.json    everything the publish call needs
    data/drafts/media/<id>.jpg|mp4   the WINNER's media only (one per day
                                     per account — small enough for git,
                                     and the queue must survive provider
                                     URLs expiring)
    data/drafts/resolved/<id>.json   the same record, stamped with the
                                     outcome — the audit trail

Resolving moves the pending file locally; the cycle side never re-creates
an id, so an unpushed resolution can never cause a duplicate. The routine
prunes stale pending (>7 days) and old resolved (>30 days) records so the
ledger stays small.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = ROOT / "data" / "drafts"
PENDING_DIR = DRAFTS_DIR / "pending"
RESOLVED_DIR = DRAFTS_DIR / "resolved"
MEDIA_DIR = DRAFTS_DIR / "media"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def export_draft(fields: dict, media_src: str | Path | None = None) -> str:
    """Write one draft into the ledger; returns its id. `fields` must carry
    persona, platform, media_kind, text — plus whatever else release needs
    (alt, title, tags, provenance, brief_id)."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    draft_id = (f"{stamp}-{fields.get('persona', 'x')}-"
                f"{fields.get('platform', 'x')}-{uuid.uuid4().hex[:6]}")
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    media_name = ""
    if media_src:
        src = Path(media_src)
        if src.exists():
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            media_name = f"{draft_id}{src.suffix.lower()}"
            shutil.copyfile(src, MEDIA_DIR / media_name)
    record = {**fields, "id": draft_id, "media_file": media_name,
              "status": "pending", "created_at": _now()}
    (PENDING_DIR / f"{draft_id}.json").write_text(
        json.dumps(record, indent=2, default=str), encoding="utf-8")
    return draft_id


def pending() -> list[dict]:
    """Every unresolved draft, oldest first. A corrupt file is skipped, never
    fatal — one bad export must not hide the rest of the queue."""
    out = []
    if not PENDING_DIR.exists():
        return out
    for p in sorted(PENDING_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda d: d.get("created_at", ""))
    return out


def get(draft_id: str) -> dict | None:
    p = PENDING_DIR / f"{draft_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def media_path(draft: dict) -> Path | None:
    """The committed winner media for a draft, when it exists locally."""
    name = draft.get("media_file") or ""
    if not name:
        return None
    p = MEDIA_DIR / name
    return p if p.exists() else None


def edit_text(draft_id: str, text: str) -> dict:
    """The operator's pen on a held draft: replace the caption of a PENDING
    record in place, stamped so the audit trail shows a human touched it.
    A small wording fix must not force a reject. The mechanical disclosure
    and the platform limit are still applied at release, on the edited text
    — so the operator can never edit the disclosure away."""
    text = (text or "").strip()
    if not text:
        raise ValueError("edited text is empty — reject the draft instead")
    d = get(draft_id)
    if d is None:
        raise FileNotFoundError(f"no pending draft '{draft_id}'")
    d["text"] = text
    d["edited_at"] = _now()
    (PENDING_DIR / f"{draft_id}.json").write_text(
        json.dumps(d, indent=2, default=str), encoding="utf-8")
    return d


def resolved(limit: int = 40) -> list[dict]:
    """Every answered draft, newest first — the queue's other half.

    This is the only publishing record that crosses machines: a post row
    lives in the local database of whichever machine released it, and its
    lineage joins to a cycle the other machine never ran. The ledger travels
    by git, so both machines can always answer 'what went out, and where is
    it?'."""
    out = []
    if not RESOLVED_DIR.exists():
        return out
    for p in RESOLVED_DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda d: d.get("resolved_at", ""), reverse=True)
    return out[:limit]


def recent_rejections(persona_id: str, platform: str = "",
                      limit: int = 5) -> list[str]:
    """Why this persona's last drafts were turned down, newest first. The
    operator's reason is the only feedback the studio ever gets from the one
    person who sees every draft — a queue that collects it and never reads
    it back is just a complaint box."""
    if not RESOLVED_DIR.exists():
        return []
    out = []
    for p in sorted(RESOLVED_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("status") != "rejected" or d.get("persona") != persona_id:
            continue
        if platform and d.get("platform") != platform:
            continue
        note = (d.get("note") or "").strip()
        # the default stamp carries no information — don't teach on it
        if note and note != "rejected by operator":
            out.append(note)
        if len(out) >= limit:
            break
    return out


def stamp_error(draft_id: str, message: str) -> None:
    """A failed release attempt leaves its reason ON the pending record — the
    draft is never consumed by a failure, and the card can show why the last
    try didn't land."""
    d = get(draft_id)
    if d is None:
        return
    d["last_error"] = message
    d["last_error_at"] = _now()
    (PENDING_DIR / f"{draft_id}.json").write_text(
        json.dumps(d, indent=2, default=str), encoding="utf-8")


def resolve(draft_id: str, status: str, note: str = "") -> None:
    """Stamp the outcome and move the record out of the queue. Media stays
    for the audit trail; the routine prunes it with the resolved record."""
    d = get(draft_id)
    if d is None:
        raise FileNotFoundError(f"no pending draft '{draft_id}'")
    d.update(status=status, note=note, resolved_at=_now())
    RESOLVED_DIR.mkdir(parents=True, exist_ok=True)
    (RESOLVED_DIR / f"{draft_id}.json").write_text(
        json.dumps(d, indent=2, default=str), encoding="utf-8")
    (PENDING_DIR / f"{draft_id}.json").unlink()


if __name__ == "__main__":
    for d in pending() or [{"id": "(queue is empty)"}]:
        print(f"  {d['id']}"
              + (f"  {d.get('persona')}/{d.get('platform')}"
                 if d.get('persona') else ""))
