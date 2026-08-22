"""Read-only truth check: does the running console SHOW what the ledger says?

Rendering without an error is not correctness. The Pipeline screen rendered
cleanly for days while reading an empty machine-local database — the
operator put it best, three days before a CEO demo: "o kadar işlem yaptık,
pipeline hep boş gözüküyor." Fifty smoke tests asserted "renders"; none
asserted "tells the truth". This script closes that class: it fetches the
console's own API and compares every number against the git ledger the
console is supposed to be reading.

Safe anywhere: it only GETs. Run it against any running console —

    python tools/truthpass.py                       # local console, :8377
    python tools/truthpass.py --url http://127.0.0.1:8398
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import draftpool, ledgerview  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8377")
    base = ap.parse_args().url.rstrip("/")

    state = get(f"{base}/api/state")
    drafts = get(f"{base}/api/drafts")
    pool = get(f"{base}/api/pool")

    # ── the ledger's own numbers, computed independently ────────
    totals = ledgerview.totals()
    published = ledgerview.published(20)
    gallery = ledgerview.media_gallery(36)
    pending = draftpool.pending()

    prod = state.get("production") or {}
    check("pipeline totals match the ledger", prod.get("totals") == totals,
          f"screen {prod.get('totals')} vs ledger {totals}")
    check("pipeline lists the cycle reports",
          len(prod.get("runs") or []) == min(14, totals["cycles"]))
    check("every run row carries a report file",
          all(r.get("file", "").endswith(".md") for r in prod.get("runs") or []))

    check("content screen has the published work",
          len(prod.get("published") or []) == len(published),
          f"screen {len(prod.get('published') or [])} vs ledger {len(published)}")
    if published:
        check("published entries keep their captions",
              all(p.get("text") for p in prod["published"]))
        check("at least one published link survives",
              any(p.get("url") for p in prod["published"]) ==
              any(p.get("url") for p in published))

    check("assets gallery walks the ledger media",
          len(prod.get("gallery") or []) == len(gallery),
          f"screen {len(prod.get('gallery') or [])} vs ledger {len(gallery)}")
    check("gallery entries exist on disk",
          all(Path(x["path"]).exists() for x in prod.get("gallery") or []))

    check("approvals badge equals the pending ledger",
          state.get("drafts_pending") == len(pending),
          f"badge {state.get('drafts_pending')} vs ledger {len(pending)}")
    qd = drafts.get("drafts") if isinstance(drafts, dict) else drafts
    check("approvals queue serves every pending draft",
          len(qd or []) == len(pending))
    check("every queued draft carries its final text",
          all(d.get("final_text") for d in qd or []))

    # posted-today only where the gate reaches the counting branch — a
    # machine without credentials reports 0 by design, honestly labeled
    for a in state.get("accounts") or []:
        gate = a.get("gate") or {}
        if a.get("platform") == "instagram" and gate.get("kind") not in (
                "credentials", "status", "policy"):
            lg = ledgerview.published_today("instagram")[0]
            check("instagram posted-today is at least the ledger's count",
                  (gate.get("posts_today") or 0) >= lg,
                  f"gate {gate.get('posts_today')} vs ledger {lg}")

    cats = pool.get("categories") or []
    check("signal pool categories are present", len(cats) > 0)
    check("harvested signals are visible",
          sum(c.get("kept", 0) for c in cats) > 0)

    print(f"\n{'ALL TRUE' if not FAILURES else f'{len(FAILURES)} LIES'} — "
          f"console at {base} vs the git ledger")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
