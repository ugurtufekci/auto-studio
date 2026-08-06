"""Publish guardrails — config/platform_policy.yaml enforced in code.

The suspension postmortem in one module: cadence caps, warm-up curve,
minimum gaps, jitter, caption dedupe. run.py consults this BEFORE spending
money on rendering and BEFORE any authenticated call.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_policy(platform: str = "bluesky") -> dict:
    with open(CONFIG_DIR / "platform_policy.yaml") as f:
        return yaml.safe_load(f)[platform]


def _parse_ts(value: str) -> datetime:
    """Timestamps in the DB may be tz-aware ISO (written by this code) or naive
    (written by SQL helpers/migrations). Normalise both to UTC-aware."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _account_age_days(con) -> float:
    """Age since (re)provisioning — personas row for the real account."""
    row = con.execute(
        "SELECT created_at FROM personas WHERE demo=0 ORDER BY id LIMIT 1").fetchone()
    if not row or not row["created_at"]:
        return 0.0
    born = _parse_ts(row["created_at"])
    return (datetime.now(UTC) - born).total_seconds() / 86400


def warmup_cap(policy: dict, age_days: float) -> int:
    for stage in policy["warmup"]:
        if age_days < stage["days"]:
            return min(stage["posts_per_day"], policy["hard_max_posts_per_day"])
    return policy["hard_max_posts_per_day"]


def can_post(con, platform: str = "bluesky",
             policy: dict | None = None) -> tuple[bool, str]:
    """(ok, reason) for ONE platform. Risk differs per platform, so the policy
    does too — a Bluesky warm-up must never gate a Telegram channel post.
    Checked at cycle start; a blocked cycle costs nothing."""
    policy = policy or load_policy(platform)
    age = _account_age_days(con)
    cap = warmup_cap(policy, age)
    if cap == 0:
        return False, (f"{platform}: warm-up — account is {age:.1f} days old, "
                       f"automation stays silent for the first "
                       f"{policy['warmup'][0]['days']} days")
    published_today = con.execute(
        "SELECT COUNT(*) FROM posts WHERE status='published' AND platform=? "
        "AND date(posted_at)=date('now')", (platform,)).fetchone()[0]
    if published_today >= cap:
        return False, (f"{platform}: cadence cap reached — {published_today}/{cap} "
                       f"posts today")
    last = con.execute(
        "SELECT posted_at FROM posts WHERE status='published' AND platform=? "
        "ORDER BY id DESC LIMIT 1", (platform,)).fetchone()
    if last and last["posted_at"]:
        gap_h = (datetime.now(UTC)
                 - _parse_ts(last["posted_at"])).total_seconds() / 3600
        if gap_h < policy["min_gap_hours"]:
            return False, (f"{platform}: min-gap — last post {gap_h:.1f}h ago, "
                           f"policy requires {policy['min_gap_hours']}h")
    return True, f"{platform}: ok ({published_today}/{cap} used today)"


def allowed_platforms(con, targets: list[str]) -> tuple[list[str], list[str]]:
    """Split requested targets into (allowed, blocked-with-reason)."""
    ok, blocked = [], []
    for p in targets:
        try:
            allowed, reason = can_post(con, p)
        except KeyError:
            allowed, reason = False, f"{p}: no policy section in platform_policy.yaml"
        (ok if allowed else blocked).append(p if allowed else reason)
    return ok, blocked


def jitter_minutes(policy: dict | None = None) -> int:
    policy = policy or load_policy()
    return random.randint(0, policy.get("jitter_minutes", 0))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def is_duplicate_caption(con, caption: str, policy: dict | None = None) -> bool:
    policy = policy or load_policy()
    n = policy.get("caption_dedupe_window", 30)
    recent = con.execute(
        "SELECT text FROM posts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    target = _norm(caption)
    for r in recent:
        if not r["text"]:
            continue
        if _norm(r["text"]) == target:
            return True
    return False


def reset_warmup(con):
    """Call after a reinstatement — the account re-enters the warm-up curve."""
    now = datetime.now(UTC).isoformat()
    con.execute("UPDATE personas SET created_at=? WHERE demo=0", (now,))
    con.commit()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from studio import store
    con = store.connect()
    if "--reset-warmup" in sys.argv:
        reset_warmup(con)
        print("warm-up clock reset to now — automation silent for 48h, then 1/day")
    else:
        ok, reason = can_post(con)
        print(f"can_post: {ok} — {reason}")
        print(f"jitter draw: {jitter_minutes()} min")
