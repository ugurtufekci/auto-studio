"""Engagement metrics — the feedback half of the publish loop.

Same architecture as the signal pool, deliberately: measurement is SHARED and
unauthenticated, consumers just read. The cloud harvest routine calls
`--write` twice a day and commits the result, so performance history lives in
git next to the signal pools — it survives laptop changes, works on any fresh
clone, and no per-account deployment has to run its own collector.

    config/accounts.yaml                          who to measure (fleet registry)
    data/metrics/<platform>--<handle>/latest.json current snapshot, per-post detail
    data/metrics/<platform>--<handle>/history.jsonl one compact line per capture

Sources are public only — Bluesky's AppView and the channel's public t.me
page; the Telegram bot token, when present, adds the subscriber count and
nothing else. A suspended account is a finding, not an error: it is recorded
in the history ledger so outage periods stay visible.

Per-post history is not kept — latest.json refreshes per capture, and the
history line carries account-level aggregates. That keeps the ledger a few
hundred bytes per capture while still answering both questions that matter:
"how is the account trending" (history) and "what worked" (latest).

  python -m studio.metrics            # measure the fleet, print the summary
  python -m studio.metrics --write    # also persist latest.json + history
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from studio import persona

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "accounts.yaml"
METRICS_DIR = ROOT / "data" / "metrics"

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# The ops console polls; one fetch a minute is plenty for two public sources.
CACHE_TTL = 60.0
_cache: dict = {"t": 0.0, "data": None}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _num(s: str) -> int:
    """'2' → 2 · '1.2K' → 1200 · '3M' → 3000000 (t.me view counters)."""
    s = s.strip().replace(",", "")
    mult = 1
    if s[-1:].upper() == "K":
        mult, s = 1_000, s[:-1]
    elif s[-1:].upper() == "M":
        mult, s = 1_000_000, s[:-1]
    return int(float(s) * mult)


# ── fleet registry ──────────────────────────────────────────────

def fleet_accounts() -> list[dict]:
    """Rows from config/accounts.yaml, each enriched with its persona's
    category — the persona owns that fact, the registry just points at it.

    Falls back to deriving legs from the default persona + env so a checkout
    that predates the registry still works."""
    try:
        rows = (yaml.safe_load(REGISTRY.read_text()) or {}).get("accounts") or []
        rows = [r for r in rows
                if r.get("persona") and r.get("platform") and r.get("handle")]
        # YAML parses a bare 2026-08-06 into a date object, which is not JSON
        # serialisable — and these rows are merged into the records the ledger
        # writes. Normalise at the boundary so no consumer has to know.
        for r in rows:
            for key in ("opened_at", "suspended_at"):
                if r.get(key) is not None:
                    r[key] = str(r[key])
            if not r.get("category"):
                try:
                    r["category"] = persona.category_of(r["persona"])
                except Exception as e:
                    print(f"  [metrics] persona '{r['persona']}' unreadable: {str(e)[:60]}")
                    r["category"] = ""
        if rows:
            return rows
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [metrics] registry unreadable ({str(e)[:60]}) — falling back")
    rows = []
    try:
        p = persona.load()
        name = p.get("id") or (p.get("identity") or {}).get("name", "persona").lower()
        category = (p.get("content") or {}).get("category", "")
        handle = os.environ.get("BLUESKY_HANDLE") or (p.get("identity") or {}).get("handle")
        if handle:
            rows.append({"persona": name, "platform": "bluesky",
                         "handle": handle, "category": category})
        channel = os.environ.get("TELEGRAM_CHANNEL", "").lstrip("@")
        if channel:
            rows.append({"persona": name, "platform": "telegram",
                         "handle": channel, "category": category})
    except Exception:
        pass
    return rows


# ── bluesky: public AppView, no auth ────────────────────────────

def map_bluesky_feed(feed: dict, handle: str) -> list[dict]:
    """getAuthorFeed payload → per-post engagement rows (pure, testable)."""
    posts = []
    for item in feed.get("feed") or []:
        p = item.get("post") or {}
        uri = p.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        posts.append({
            "ref": uri,
            "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else "",
            "views": None,  # bluesky does not expose views
            "likes": p.get("likeCount") or 0,
            "reposts": p.get("repostCount") or 0,
            "replies": p.get("replyCount") or 0,
            "created_at": ((p.get("record") or {}).get("createdAt") or "")[:16],
        })
    return posts


def fetch_bluesky(handle: str) -> dict:
    out = {"platform": "bluesky", "handle": handle, "status": "ok",
           "followers": None, "posts": []}
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                      params={"actor": handle}, headers=UA, timeout=15)
        if r.status_code != 200:
            out["status"] = ("suspended" if "AccountTakedown" in r.text
                             else "not_found" if r.status_code == 400
                             else f"error http {r.status_code}")
            return out
        out["followers"] = r.json().get("followersCount")
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                      params={"actor": handle, "limit": 30}, headers=UA, timeout=15)
        r.raise_for_status()
        out["posts"] = map_bluesky_feed(r.json(), handle)
    except Exception as e:
        out["status"] = f"error {str(e)[:60]}"
    return out


# ── telegram: public t.me page + optional bot API ───────────────

def parse_tme_subscribers(html: str) -> int | None:
    """Subscriber count off the public channel page (pure, testable).

    Two markup shapes carry it — the /s/ preview page's counter block and the
    plain channel page's extras line. Parsing the page (rather than asking the
    bot API) keeps the whole capture credential-free, which is what lets the
    cloud harvest record follower growth at all: the harvest environment has
    no bot token, and a token-only path silently logged followers=null there.
    """
    m = re.search(r'tgme_channel_info_counter"[^>]*>\s*<span class="counter_value">'
                  r'\s*([\d.,]+[KM]?)\s*</span>\s*<span class="counter_type">\s*'
                  r'subscriber', html)
    if not m:
        m = re.search(r'tgme_page_extra"[^>]*>\s*([\d.,]+[KM]?)\s+subscriber', html)
    if not m:
        return None
    try:
        return _num(m.group(1))
    except ValueError:
        return None


def parse_tme(html: str) -> list[dict]:
    """Public t.me/s/<channel> page → per-post views (pure, testable).
    Service messages carry no view counter and are skipped."""
    posts = []
    chunks = re.split(r'data-post="([\w-]+/\d+)"', html)
    for i in range(1, len(chunks) - 1, 2):
        ref, body = chunks[i], chunks[i + 1]
        views = re.search(r'tgme_widget_message_views">\s*([\d.,]+[KM]?)', body)
        if not views:
            continue
        stamp = re.search(r'datetime="([^"]+)"', body)
        posts.append({
            "ref": ref,
            "url": f"https://t.me/{ref}",
            "views": _num(views.group(1)),
            "likes": None, "reposts": None, "replies": None,
            "created_at": (stamp.group(1) if stamp else "")[:16],
        })
    return posts


def fetch_telegram(handle: str) -> dict:
    """One request to the public preview page yields both the subscriber count
    and per-post views. The bot token is only a fallback for the count, so a
    tokenless environment (the cloud harvest) still records follower growth."""
    out = {"platform": "telegram", "handle": handle, "status": "ok",
           "followers": None, "posts": []}
    name = handle.lstrip("@")
    try:
        r = httpx.get(f"https://t.me/s/{name}", headers=UA, timeout=15,
                      follow_redirects=True)
        r.raise_for_status()
        out["followers"] = parse_tme_subscribers(r.text)
        out["posts"] = parse_tme(r.text)
    except Exception as e:
        out["status"] = f"error {str(e)[:60]}"
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if out["followers"] is None and token:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getChatMemberCount",
                          params={"chat_id": f"@{name}"}, timeout=15)
            if r.status_code == 200 and r.json().get("ok"):
                out["followers"] = r.json()["result"]
        except Exception:
            pass
    return out


# ── instagram: Graph API, the one platform that needs a credential ──

def map_instagram_media(payload: dict, handle: str) -> list[dict]:
    """/media payload → per-post engagement rows (pure, testable)."""
    posts = []
    for m in payload.get("data") or []:
        posts.append({
            "ref": m.get("id") or "",
            "url": m.get("permalink") or f"https://www.instagram.com/{handle}/",
            "views": m.get("view_count"),
            "likes": m.get("like_count"),
            "reposts": None,
            "replies": m.get("comments_count"),
            "created_at": (m.get("timestamp") or "")[:16],
        })
    return posts


def fetch_instagram(handle: str) -> dict:
    """Unlike Bluesky and Telegram, Instagram has no public surface we can read
    — profile pages sit behind a login wall — so this is the one leg that
    cannot be measured credential-free. That has a consequence worth stating:
    the cloud harvest has no token, so Instagram history only accumulates when
    a capture runs somewhere the credentials exist."""
    out = {"platform": "instagram", "handle": handle, "status": "ok",
           "followers": None, "posts": []}
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.environ.get("INSTAGRAM_USER_ID")
    if not (token and user_id):
        out["status"] = "needs credentials"
        return out
    base = "https://graph.instagram.com/v21.0"
    try:
        r = httpx.get(f"{base}/{user_id}",
                      params={"fields": "followers_count,media_count",
                              "access_token": token}, timeout=15)
        if r.status_code != 200:
            out["status"] = f"error http {r.status_code}"
            return out
        out["followers"] = r.json().get("followers_count")
        r = httpx.get(f"{base}/{user_id}/media",
                      params={"fields": "id,permalink,timestamp,like_count,"
                                        "comments_count",
                              "limit": 30, "access_token": token}, timeout=15)
        r.raise_for_status()
        out["posts"] = map_instagram_media(r.json(), handle)
    except Exception as e:
        out["status"] = f"error {str(e)[:60]}"
    return out


FETCHERS = {"bluesky": fetch_bluesky, "telegram": fetch_telegram,
            "instagram": fetch_instagram}


# ── collect ─────────────────────────────────────────────────────

def collect(force: bool = False) -> dict:
    """Measure every registry account. One account failing is that account's
    status, never the run's."""
    if not force and _cache["data"] and time.time() - _cache["t"] < CACHE_TTL:
        return _cache["data"]
    measured = []
    for acct in fleet_accounts():
        fetch = FETCHERS.get(acct["platform"])
        if not fetch:
            measured.append({**acct, "status": "unmeasurable", "followers": None,
                             "posts": []})
            continue
        measured.append({**acct, **fetch(acct["handle"])})
    data = {"accounts": measured, "captured_at": _now()}
    _cache.update(t=time.time(), data=data)
    return data


# ── the git-backed pool ─────────────────────────────────────────

def _acct_dir(base: Path, acct: dict) -> Path:
    return base / f"{acct['platform']}--{acct['handle'].replace('/', '_')}"


def persist_pool(data: dict, base: Path = METRICS_DIR) -> list[Path]:
    """latest.json per account (full detail, overwritten) + one compact line
    appended to its history.jsonl (the append-only ledger git carries).

    Each account is persisted independently: the same principle collection
    follows, applied to writing. One account whose record cannot be written
    must not cost the whole capture — the harvest that calls this has no
    second chance until the next run."""
    written = []
    for acct in data["accounts"]:
        try:
            d = _acct_dir(base, acct)
            d.mkdir(parents=True, exist_ok=True)
            # default=str keeps an unexpected scalar (a YAML date, a Decimal)
            # from turning one odd registry field into a lost capture
            (d / "latest.json").write_text(json.dumps(
                {**acct, "captured_at": data["captured_at"]},
                indent=2, default=str))
            posts = acct.get("posts") or []
            views = [p["views"] for p in posts if p.get("views") is not None]
            likes = [p["likes"] for p in posts if p.get("likes") is not None]
            line = {
                "ts": data["captured_at"],
                "status": acct["status"],
                "followers": acct.get("followers"),
                "posts_tracked": len(posts),
                "views_total": sum(views) if views else None,
                "likes_total": sum(likes) if likes else None,
            }
            with open(d / "history.jsonl", "a") as f:
                f.write(json.dumps(line) + "\n")
            written.append(d)
        except Exception as e:
            print(f"  [metrics] could not persist "
                  f"{acct.get('platform')}--{acct.get('handle')}: {str(e)[:80]}")
    return written


def read_history(platform: str, handle: str, base: Path = METRICS_DIR,
                 limit: int = 400) -> list[dict]:
    """The account's ledger, oldest→newest. Tolerates a corrupt line — one bad
    write must never blind the whole trend."""
    path = base / f"{platform}--{handle.replace('/', '_')}" / "history.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def write_pool(base: Path = METRICS_DIR) -> dict:
    data = collect(force=True)
    persist_pool(data, base)
    return data


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    write = "--write" in sys.argv
    data = write_pool() if write else collect(force=True)
    for a in data["accounts"]:
        head = f"── {a['persona']} · {a['platform']} ({a['handle']})"
        if a["status"] != "ok":
            print(f"{head} — {a['status'].upper()}")
            continue
        print(f"{head} — {a['followers']} followers · {len(a['posts'])} posts")
        for p in a["posts"][:8]:
            eng = (f"{p['views']} views" if p["views"] is not None
                   else f"{p['likes']}♥ {p['reposts']}↻ {p['replies']}💬")
            print(f"   {p['created_at']:<16} {eng:<18} {p['url']}")
    if write:
        print(f"\npool written under {METRICS_DIR}")
