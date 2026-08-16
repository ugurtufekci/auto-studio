"""Which style is working — measured, then acted on.

The studio can shoot several named styles (config/formats/*.yaml). Without
this module the choice is a habit: whatever the persona's default happens to
be, forever. With it the choice is evidence, and the evidence is the only
thing that can tell a format that reaches 150 strangers and moves none of
them from one that reaches 150 and earns three saves.

Attribution is by CAPTION, not by id, and that is not laziness: the operator
publishes by hand from the console, so the published post carries no id we
issued. Its caption, though, is the one we wrote.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from studio import draftpool, metrics

METRICS_DIR = metrics.METRICS_DIR

# What a post is WORTH, in units of reach. These are a judgement, not a
# measurement: a save means someone intends to come back, a share means they
# spent their own name on it, and both push distribution far harder than a
# like. Edit them here rather than scattering weights through the code.
WORTH = {"reach": 1.0, "saved": 100.0, "shares": 150.0, "likes": 10.0,
         "replies": 40.0}

# How much of the choice stays curious. A style that lost once is not a style
# that cannot win: audiences change, and one bad post is a small sample.
EXPLORATION = 0.25


def _norm(text: str) -> str:
    """A caption reduced to what survives publishing.

    The published version carries the disclosure and a trimmed hashtag tail,
    so only the prose is comparable — lowercased, punctuation dropped,
    whitespace collapsed."""
    text = re.sub(r"#\S+", " ", str(text or ""))
    text = text.replace("🤖", " ").replace("AI-generated", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def measured_posts(handle: str, base: Path | None = None) -> list[dict]:
    """The last capture's posts for one account."""
    path = (base or METRICS_DIR) / f"instagram--{handle}" / "latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("posts") or []
    except Exception:
        return []


def post_worth(post: dict) -> float:
    """One post's performance as a single number."""
    return sum(w * float(post.get(k) or 0) for k, w in WORTH.items())


def attribute(persona_id: str, handle: str,
              base: Path | None = None) -> list[dict]:
    """Published posts joined to the style that produced them.

    A draft's text is matched against a published caption by prefix — the
    first forty comparable characters are already unique between posts, and
    matching further would break on the trimming Instagram does."""
    # PENDING drafts count too, and that is the point: this operator
    # publishes by hand from the file rather than pressing Approve, so a
    # post that is live on the account can still be sitting in the queue.
    # Matching only approved records would attribute almost nothing.
    drafts = [d for d in (draftpool.pending() + draftpool.resolved())
              if d.get("persona") == persona_id
              and d.get("status") != "rejected"]
    rows = []
    for post in measured_posts(handle, base):
        cap = _norm(post.get("caption"))
        if not cap:
            continue
        for d in drafts:
            head = _norm(d.get("text"))[:40]
            if head and cap.startswith(head):
                fmt = (d.get("provenance") or {}).get("format") or ""
                rows.append({**post, "format": fmt, "draft_id": d.get("id"),
                             "worth": post_worth(post)})
                break
    return rows


def format_scores(persona_id: str, handle: str,
                  base: Path | None = None) -> dict[str, dict]:
    """Per style: how many posts, and what they were worth on average."""
    out: dict[str, dict] = {}
    for row in attribute(persona_id, handle, base):
        fmt = row["format"] or "unattributed"
        acc = out.setdefault(fmt, {"posts": 0, "worth": 0.0, "reach": 0,
                                   "saved": 0, "likes": 0})
        acc["posts"] += 1
        acc["worth"] += row["worth"]
        for k in ("reach", "saved", "likes"):
            acc[k] += int(row.get(k) or 0)
    for acc in out.values():
        acc["score"] = acc["worth"] / max(acc["posts"], 1)
    return out


def choose(persona_id: str, handle: str, allowed: list[str],
           base: Path | None = None,
           rng: random.Random | None = None) -> tuple[str, str]:
    """Pick a style to shoot, and say why in one line.

    An unmeasured style is shot first: a format with no posts is not a
    format that failed, and the only way to compare it is to run it once.
    After that the pick is weighted by measured worth, blended with a flat
    share so a style that lost early still gets its turn.
    """
    allowed = [a for a in allowed if a]
    if not allowed:
        return "", "no styles configured"
    if len(allowed) == 1:
        return allowed[0], f"{allowed[0]} is the only style adopted"

    scores = format_scores(persona_id, handle, base)
    unmeasured = [a for a in allowed if a not in scores]
    if unmeasured:
        pick = unmeasured[0]
        return pick, (f"{pick} has no measured posts yet — a style with no "
                      f"evidence is shot before it is judged")

    rng = rng or random.Random()
    worths = [max(scores[a]["score"], 0.0) for a in allowed]
    total = sum(worths) or 1.0
    flat = 1.0 / len(allowed)
    weights = [(1 - EXPLORATION) * (w / total) + EXPLORATION * flat
               for w in worths]
    pick = rng.choices(allowed, weights=weights)[0]
    best = max(allowed, key=lambda a: scores[a]["score"])
    detail = " · ".join(
        f"{a} {scores[a]['score']:.0f} from {scores[a]['posts']} post"
        f"{'s' if scores[a]['posts'] != 1 else ''}" for a in allowed)
    lead = "leading" if pick == best else "exploring against the leader"
    return pick, f"{pick} ({lead}) — {detail}"
