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


def registry_account(platform: str) -> dict | None:
    """The fleet-registry row for a platform, or None if it has no entry."""
    from studio import metrics
    for acct in metrics.fleet_accounts():
        if acct.get("platform") == platform:
            return acct
    return None


def _account_age_days(con, platform: str = "bluesky") -> float:
    """Days since the PLATFORM account was opened.

    Read from config/accounts.yaml (version-controlled, survives machine
    changes) and only then from the local personas row. The database is
    machine-local: after a fresh clone it used to report a months-old account
    as newborn, silently restarting a warm-up that was long finished. An
    unknown age returns 0.0 — the conservative direction, since age 0 means
    the warm-up curve keeps automation silent."""
    acct = registry_account(platform) or {}
    opened = acct.get("opened_at")
    if opened:
        try:
            return (datetime.now(UTC) - _parse_ts(str(opened))).total_seconds() / 86400
        except ValueError:
            print(f"  [guard] unparseable opened_at for {platform}: {opened!r}")
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


def platform_activity(platform: str, handle: str) -> tuple[int, str | None]:
    """What the PLATFORM says we posted: (count today, latest timestamp).

    The local database is machine-local, so after a laptop change it reports
    zero posts today and the cadence cap resets — the account could be posted
    to twice its limit on the day of a move. The platform's own feed cannot
    drift that way, and it has a second virtue: a post the operator made by
    hand counts too, which is what cadence is actually about.

    Network failure returns (0, None) so the caller falls back to its own
    memory rather than treating an outage as permission."""
    try:
        from studio import metrics
        fetch = metrics.FETCHERS.get(platform)
        if not fetch:
            return 0, None
        data = fetch(handle)
        if data.get("status") != "ok":
            return 0, None
        today = datetime.now(UTC).date().isoformat()
        stamps = [p.get("created_at") or "" for p in data.get("posts") or []]
        stamps = [t for t in stamps if t]
        return sum(1 for t in stamps if t[:10] == today), (max(stamps) if stamps else None)
    except Exception as e:
        print(f"  [guard] {platform}: could not read platform activity "
              f"({str(e)[:60]}) — falling back to local history")
        return 0, None


def can_post(con, platform: str = "bluesky",
             policy: dict | None = None) -> tuple[bool, str]:
    """(ok, reason) for ONE platform. Risk differs per platform, so the policy
    does too — a Bluesky warm-up must never gate a Telegram channel post.
    Checked at cycle start; a blocked cycle costs nothing."""
    policy = policy or load_policy(platform)
    # A registry status other than active outranks every other check: valid
    # credentials do not mean an account may be posted to. Publishing into a
    # takedown produces exactly the retry pattern moderation reads as evasion.
    status = (registry_account(platform) or {}).get("status", "active")
    if status != "active":
        return False, (f"{platform}: account status is '{status}' in "
                       f"config/accounts.yaml — publishing is blocked until it "
                       f"reads 'active'")
    age = _account_age_days(con, platform)
    cap = warmup_cap(policy, age)
    if cap == 0:
        return False, (f"{platform}: warm-up — account is {age:.1f} days old, "
                       f"automation stays silent for the first "
                       f"{policy['warmup'][0]['days']} days")
    # Cadence is judged against whichever source knows about MORE activity —
    # the platform when our database has been reset by a machine change, our
    # database when the platform is unreachable. Never the smaller number.
    local_today = con.execute(
        "SELECT COUNT(*) FROM posts WHERE status='published' AND platform=? "
        "AND date(posted_at)=date('now')", (platform,)).fetchone()[0]
    handle = (registry_account(platform) or {}).get("handle", "")
    remote_today, remote_last = platform_activity(platform, handle) if handle else (0, None)
    published_today = max(local_today, remote_today)
    if published_today >= cap:
        seen = "platform" if remote_today > local_today else "local history"
        return False, (f"{platform}: cadence cap reached — {published_today}/{cap} "
                       f"posts today (per {seen})")

    stamps = []
    row = con.execute(
        "SELECT posted_at FROM posts WHERE status='published' AND platform=? "
        "ORDER BY id DESC LIMIT 1", (platform,)).fetchone()
    if row and row["posted_at"]:
        stamps.append(_parse_ts(row["posted_at"]))
    if remote_last:
        try:
            stamps.append(_parse_ts(remote_last))
        except ValueError:
            pass
    if stamps:
        gap_h = (datetime.now(UTC) - max(stamps)).total_seconds() / 3600
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
