"""Fleet health — the operator's picture, built from the same authorities
the pipeline itself consults.

The console used to show one red/green "provider" dot per platform. That
answers a machine question ("does this machine hold a key for X") while
reading like an account question ("is X okay") — and the moment two personas
share a platform, one suspended and one live, a single dot per platform
cannot be true of anything. So the picture is split along ownership lines,
because ownership is what decides who has to act:

  account_cards()    one card per registry account — registry status,
                     credentials, and the publish gate. HEALTH LIVES HERE,
                     per account, never per platform.
  attention()        the operator's inbox: everything that needs a human,
                     hardest deadline first. States the system resolves by
                     itself (warm-up, min-gap, cadence) are never in it.
  shared_services()  what the whole fleet stands on: the brain, the image
                     provider, the two git-committed harvests. Red here
                     means every persona is affected at once.
  machine_keys()     which platform credentials THIS machine holds, and
                     which registry accounts each key serves. A deployment
                     fact, not health — a missing key on a laptop that
                     never publishes is not an incident.

Gate verdicts here are the LOCAL view (registry + policy + this machine's
own history). The publish run re-checks against the platform itself via
guard.can_post — the console must not hit platform APIs on every refresh.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from studio import guard, metrics, pool

# Appeal windows we have actually verified from platform documentation.
# Bluesky: "within two weeks" per its moderation notices (docs/account-safety.md §5).
APPEAL_WINDOW_DAYS = {"bluesky": 14}

# An active, unblocked account that has published before but not in this long
# is drifting silent — worth a look, not an alarm.
SILENT_AFTER_HOURS = 36.0

# Both harvests are cloud routines committing twice a day; one missed run is
# jitter, two is a stopped routine.
HARVEST_STALE_HOURS = pool.STALE_AFTER_HOURS
LEDGER_STALE_HOURS = 26.0

_SEV_RANK = {"critical": 0, "action": 1, "watch": 2}


def _ts(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _persona_name(persona_id: str) -> str:
    try:
        from studio import persona
        return persona.load(persona_id)["identity"]["name"]
    except Exception:
        return persona_id.title()


# ── credentials: a machine fact, per platform ───────────────────

def platform_credentials(platform: str) -> tuple[bool, str]:
    """(present, note) for THIS machine's credentials for a platform.

    Presence only — whether the account behind them may be published to is
    the registry's and the guard's business, never inferred from a key."""
    env = os.environ.get
    if platform == "bluesky":
        return bool(env("BLUESKY_APP_PASSWORD")), ""
    if platform == "telegram":
        return (all(env(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL")),
                "")
    if platform == "mastodon":
        return all(env(k) for k in ("MASTODON_INSTANCE", "MASTODON_TOKEN")), ""
    if platform == "youtube":
        return (all(env(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                                     "YOUTUBE_REFRESH_TOKEN")), "")
    if platform == "instagram":
        return _instagram_credentials()
    return False, "no adapter for this platform"


def _instagram_credentials() -> tuple[bool, str]:
    """Instagram's key has a lifetime, so its note carries the clock.

    A media host is deliberately NOT required here: generated stills publish
    straight from the provider's own URL (Meta fetches once and keeps a
    copy). Only locally-assembled video still needs hosting, so its absence
    is a scoped note, never a red state."""
    from studio import media_host
    from studio import publisher_instagram as ig

    if not ig.configured():
        return False, ""
    left = ig.token_days_left()
    if left is not None and left <= 0:
        return False, "token EXPIRED — re-run the OAuth flow"
    note = (f"token {left:.0f}d left" if left is not None
            else "token age unknown until first refresh")
    if not media_host.configured():
        note += " · stills publish via provider URLs; local video would need a media host"
    return True, note


# ── the publish gate: local view of guard state ─────────────────

def _local_posts(con, platform: str) -> tuple[int, str | None]:
    """(published today, latest timestamp) from this machine's history.
    Note: attribution is per platform — the posts table predates multi-account
    platforms, and today each platform has exactly one account."""
    today = con.execute(
        "SELECT COUNT(*) FROM posts WHERE status='published' AND platform=? "
        "AND date(posted_at)=date('now')", (platform,)).fetchone()[0]
    row = con.execute(
        "SELECT posted_at FROM posts WHERE status='published' AND platform=? "
        "ORDER BY id DESC LIMIT 1", (platform,)).fetchone()
    return today, (row["posted_at"] if row else None)


def _warmup_ends(policy: dict, opened_at: str) -> str:
    """When the zero-posts stage of the warm-up curve ends."""
    for stage in policy["warmup"]:
        if guard.warmup_cap(policy, float(stage["days"])) > 0:
            return (_ts(opened_at) + timedelta(days=stage["days"])).isoformat()
    return ""


def account_gate(con, acct: dict, credentials_ok: bool) -> dict:
    """Why this account will or will not publish right now, one reason deep —
    the same precedence the guard applies: registry status outranks
    credentials outranks the pacing rules."""
    platform = acct["platform"]
    status = acct.get("status", "active")
    if status != "active":
        return {"open": False, "kind": "status", "reason": f"registry: {status}",
                "until": "", "posts_today": 0, "cap": 0}
    if not credentials_ok:
        return {"open": False, "kind": "credentials",
                "reason": "no credentials on this machine",
                "until": "", "posts_today": 0, "cap": 0}
    # keys that exist but belong to a DIFFERENT account on the same platform
    # must read as closed here exactly as the guard will rule at publish time
    from studio import credentials
    mismatch = credentials.binding_error(acct.get("persona", ""), platform, acct)
    if mismatch:
        return {"open": False, "kind": "credentials",
                "reason": mismatch.split(" — ")[0],
                "until": "", "posts_today": 0, "cap": 0}
    try:
        policy = guard.load_policy(platform)
    except KeyError:
        return {"open": False, "kind": "policy",
                "reason": "no policy section in platform_policy.yaml",
                "until": "", "posts_today": 0, "cap": 0}
    opened = acct.get("opened_at")
    age = ((datetime.now(UTC) - _ts(opened)).total_seconds() / 86400
           if opened else 0.0)
    cap = guard.warmup_cap(policy, age)
    posts_today, last = _local_posts(con, platform)
    if cap == 0:
        return {"open": False, "kind": "warmup",
                "reason": f"warm-up — account is {age:.1f} days old",
                "until": _warmup_ends(policy, opened) if opened else "",
                "posts_today": posts_today, "cap": cap}
    if posts_today >= cap:
        midnight = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return {"open": False, "kind": "cadence",
                "reason": f"cadence cap — {posts_today}/{cap} today",
                "until": midnight.isoformat(),
                "posts_today": posts_today, "cap": cap}
    if last:
        gap_h = (datetime.now(UTC) - _ts(last)).total_seconds() / 3600
        if gap_h < policy["min_gap_hours"]:
            ready = _ts(last) + timedelta(hours=policy["min_gap_hours"])
            return {"open": False, "kind": "gap",
                    "reason": f"min-gap — last post {gap_h:.1f}h ago",
                    "until": ready.isoformat(),
                    "posts_today": posts_today, "cap": cap}
    return {"open": True, "kind": "ready",
            "reason": f"{posts_today}/{cap} used today", "until": "",
            "posts_today": posts_today, "cap": cap}


# ── the three panels ────────────────────────────────────────────

def account_cards(con) -> list[dict]:
    """One card per registry account — the unit health belongs to."""
    cards = []
    for acct in metrics.fleet_accounts():
        creds_ok, creds_note = platform_credentials(acct["platform"])
        cards.append({
            "persona": acct["persona"],
            "persona_name": _persona_name(acct["persona"]),
            "platform": acct["platform"],
            "handle": acct["handle"],
            "category": acct.get("category", ""),
            "status": acct.get("status", "active"),
            "opened_at": acct.get("opened_at", ""),
            "suspended_at": str(acct.get("suspended_at") or ""),
            "credentials_ok": creds_ok,
            "credentials_note": creds_note,
            "gate": account_gate(con, acct, creds_ok),
        })
    return cards


def _pool_metas() -> list[dict]:
    metas = []
    for category in pool.available_pools(pool.POOL_DIR):
        try:
            _, meta = pool.read_signals([category], pool_dir=pool.POOL_DIR)
            metas.extend(meta)
        except Exception:
            metas.append({"category": category, "stale": True, "age_hours": None,
                          "kept": 0, "expired": 0})
    return metas


def attention(con, cards: list[dict] | None = None) -> list[dict]:
    """The operator's inbox. Every item is something only a human can move;
    anything the system will resolve on its own (warm-up, min-gap, cadence)
    is banned from this list so a full inbox always MEANS something."""
    items = []
    cards = account_cards(con) if cards is None else cards

    for c in cards:
        who = f"{c['persona_name']} · {c['platform']}"
        if c["status"] == "suspended":
            due = ""
            window = APPEAL_WINDOW_DAYS.get(c["platform"])
            if window and c["suspended_at"]:
                due = (_ts(c["suspended_at"]) + timedelta(days=window)).isoformat()
            items.append({
                "severity": "critical",
                "title": f"{who} is suspended — appeal it",
                "detail": (f"@{c['handle']} · publishing is blocked by the registry; "
                           "the only legitimate path back is the platform's own "
                           "appeal (docs/account-safety.md §5)"),
                "due": due, "screen": "#/personas"})
        elif c["status"] == "active" and not c["credentials_ok"]:
            items.append({
                "severity": "action",
                "title": f"{who} has no credentials on this machine",
                "detail": (f"@{c['handle']} is active in the registry, but a "
                           "publish run from this machine will skip it — put the "
                           "platform keys in .env (fine to ignore if another "
                           "machine publishes this account)"),
                "due": "", "screen": "#/personas"})
        elif (c["status"] == "active" and c["credentials_ok"]
              and c["gate"]["kind"] == "credentials"):
            # keys exist but cannot be proven to be THIS account's — the
            # cross-posting firewall will hold every publish until fixed
            items.append({
                "severity": "action",
                "title": f"{who} keys don't match the account",
                "detail": c["gate"]["reason"] + " — set the persona-suffixed "
                          "variables in .env (see studio/credentials.py)",
                "due": "", "screen": "#/personas"})

    items.extend(_token_attention(cards))

    items.extend(_harvest_attention())

    row = con.execute("SELECT id, status, note FROM cycles "
                      "ORDER BY id DESC LIMIT 1").fetchone()
    if row and row["status"] == "failed":
        items.append({
            "severity": "action",
            "title": f"last cycle (#{row['id']}) failed",
            "detail": (row["note"] or "see the event log")[:140],
            "due": "", "screen": f"#/cycle/{row['id']}"})

    for c in cards:
        gate = c["gate"]
        if not (c["status"] == "active" and gate["open"]):
            continue
        _, last = _local_posts(con, c["platform"])
        if not last:
            continue  # never published from here — a fresh machine, not a stall
        silent_h = (datetime.now(UTC) - _ts(last)).total_seconds() / 3600
        if silent_h > SILENT_AFTER_HOURS:
            items.append({
                "severity": "watch",
                "title": f"{c['persona_name']} · {c['platform']} has gone quiet",
                "detail": (f"nothing published for {silent_h:.0f}h although the "
                           "gate is open — check the scheduler on whichever "
                           "machine publishes this account"),
                "due": "", "screen": "#/personas"})

    items.sort(key=lambda i: (_SEV_RANK.get(i["severity"], 9),
                              i["due"] or "9999", i["title"]))
    return items


def _harvest_attention() -> list[dict]:
    """Stale harvest data, reported as INCIDENTS rather than symptoms.

    Every pool stale at once is one event — the harvest routine stopped (or
    this checkout is behind) — and must be one inbox row, not one per
    category: seven rows for one cause is exactly the noise that buries a
    suspension. A single stale pool among fresh ones is different news: that
    category's harvest is failing specifically. The metrics ledger is its own
    routine, so it gets its own (watch) row."""
    items = []
    metas = _pool_metas()
    stale = [m for m in metas if m.get("stale")]
    if not metas:
        items.append({
            "severity": "action",
            "title": "no signal pools on disk",
            "detail": ("data/signals/ is empty — pull the repo or check the "
                       "trend-harvest routine; publishing has nothing to draw from"),
            "due": "", "screen": "#/signals"})
    elif len(stale) == len(metas) and len(metas) > 1:
        ages = [m["age_hours"] for m in stale if m.get("age_hours") is not None]
        items.append({
            "severity": "action",
            "title": f"trend harvest has stopped — all {len(metas)} pools are stale",
            "detail": ((f"newest pool is {min(ages):.0f}h old" if ages
                        else "pools unreadable")
                       + " against a twice-a-day cadence. Pull the repo first; "
                         "still stale after a pull means the harvest routine "
                         "itself stopped — check its runs and git pushes"),
            "due": "", "screen": "#/signals"})
    else:
        for m in stale:
            age = m.get("age_hours")
            items.append({
                "severity": "action",
                "title": f"signal pool '{m['category']}' is stale",
                "detail": ((f"last harvest {age:.0f}h ago" if age is not None
                            else "pool unreadable")
                           + " while other pools are fresh — this category's "
                             "harvest is failing specifically; check the "
                             "routine's log for it"),
                "due": "", "screen": "#/signals"})

    newest = _ledger_newest_ts()
    if newest:
        age_h = (datetime.now(UTC) - _ts(newest)).total_seconds() / 3600
        if age_h > LEDGER_STALE_HOURS:
            items.append({
                "severity": "watch",
                "title": "metrics ledger has stopped updating",
                "detail": (f"last capture {age_h:.0f}h ago against a twice-a-day "
                           "cadence — engagement history is gapping; pull the "
                           "repo or check the metrics routine"),
                "due": "", "screen": "#/performance"})
    return items


def _ledger_newest_ts() -> str | None:
    newest = None
    for f in metrics.METRICS_DIR.glob("*/history.jsonl"):
        try:
            ts = json.loads(f.read_text().splitlines()[-1]).get("ts", "")
            if ts and (newest is None or ts > newest):
                newest = ts
        except Exception:
            continue
    return newest


def _token_attention(cards: list[dict]) -> list[dict]:
    """The Instagram token dies silently after 60 days; publishing refreshes
    it inside the 10-day window, but an account that is not publishing (warm-up,
    pause) never runs that code — so the console watches the clock too."""
    if not any(c["platform"] == "instagram" for c in cards):
        return []
    from studio import publisher_instagram as ig
    if not ig.configured():
        return []  # the missing-credentials item already covers it
    left = ig.token_days_left()
    if left is None:
        return []
    handles = ", ".join(f"@{c['handle']}" for c in cards
                        if c["platform"] == "instagram")
    if left <= 0:
        return [{"severity": "critical",
                 "title": "Instagram token EXPIRED",
                 "detail": (f"{handles} cannot publish; an expired token cannot "
                            "be refreshed — re-run the OAuth flow and update "
                            "INSTAGRAM_ACCESS_TOKEN"),
                 "due": "", "screen": ""}]
    due = (datetime.now(UTC) + timedelta(days=left)).isoformat()
    if left <= 3:
        return [{"severity": "action",
                 "title": f"Instagram token dies in {left:.0f} days",
                 "detail": ("auto-refresh only runs while publishing — refresh "
                            "by hand now: python -m studio.publisher_instagram"),
                 "due": due, "screen": ""}]
    if left <= 14:
        return [{"severity": "watch",
                 "title": f"Instagram token has {left:.0f} days left",
                 "detail": ("normal — the next publish run auto-refreshes it "
                            "once it is within 10 days; this becomes an action "
                            "item at 3 days"),
                 "due": due, "screen": ""}]
    return []


def shared_services(con) -> list[dict]:
    """The fleet-wide dependencies: one bad dot here affects every persona."""
    row = con.execute(
        "SELECT detail FROM events WHERE status='failed' AND stage='error' "
        "AND cycle_id=(SELECT MAX(id) FROM cycles) ORDER BY id DESC LIMIT 1"
    ).fetchone()
    err = (row["detail"][:120] if row else "")

    if os.environ.get("LLM_PROVIDER", "claude_code") == "claude_code":
        brain = {"name": "Claude (Max plan)", "role": "signals · briefs · judge",
                 "ok": True, "note": "local CLI — no API credits"}
    else:
        brain = {"name": "Anthropic API", "role": "signals · briefs · judge",
                 "ok": bool(os.environ.get("ANTHROPIC_API_KEY")),
                 "note": err if "credit" in err.lower() else ""}

    fal = {"name": "fal.ai", "role": "images · video · voice",
           "ok": bool(os.environ.get("FAL_KEY")),
           "note": err if ("fal" in err.lower() or "balance" in err.lower()) else ""}

    metas = _pool_metas()
    ages = [m["age_hours"] for m in metas if m.get("age_hours") is not None]
    stale = [m for m in metas if m.get("stale")]
    harvest = {"name": "Signal harvest", "role": "trends → pools · 2×/day",
               "ok": bool(metas) and not stale,
               "note": (f"{len(metas)} pools · newest {min(ages):.0f}h ago"
                        if ages else "no pools on disk")}

    newest = _ledger_newest_ts()
    if newest:
        age_h = (datetime.now(UTC) - _ts(newest)).total_seconds() / 3600
        ledger = {"name": "Metrics ledger", "role": "engagement history · 2×/day",
                  "ok": age_h <= LEDGER_STALE_HOURS,
                  "note": f"last capture {age_h:.0f}h ago"}
    else:
        ledger = {"name": "Metrics ledger", "role": "engagement history · 2×/day",
                  "ok": False, "note": "no captures yet — check the metrics routine"}

    return [brain, fal, harvest, ledger]


def machine_keys() -> list[dict]:
    """Which platform credentials this machine holds, and which registry
    accounts each key serves — deployment inventory, not health."""
    accounts = metrics.fleet_accounts()
    rows = []
    for platform in ("bluesky", "telegram", "instagram", "mastodon", "youtube"):
        ok, note = platform_credentials(platform)
        serves = [a["handle"] for a in accounts if a["platform"] == platform]
        rows.append({"platform": platform, "ok": ok, "note": note,
                     "serves": serves})
    return rows


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dotenv import load_dotenv
    load_dotenv()
    from studio import store
    con = store.connect()
    print("── attention ──")
    for i in attention(con):
        print(f"  [{i['severity']:>8}] {i['title']}"
              + (f"  (due {i['due'][:10]})" if i["due"] else ""))
    print("── accounts ──")
    for c in account_cards(con):
        g = c["gate"]
        print(f"  {c['persona_name']:>6} · {c['platform']:<10} @{c['handle']}"
              f"  [{c['status']}] gate={'open' if g['open'] else g['kind']}"
              f" — {g['reason']}")
    print("── services ──")
    for s in shared_services(con):
        print(f"  {'OK ' if s['ok'] else 'BAD'} {s['name']} — {s['note']}")
