"""Engagement metrics — the feedback half of the publish loop.

P0 of the monetization roadmap: read back what every platform did with our
posts, so growth decisions and (later) signal scoring can learn from
performance instead of guessing. Collection stays unauthenticated wherever
possible — Bluesky's public AppView and the channel's public t.me page — and
the Telegram bot token only adds the subscriber count.

A suspended account is a finding, not an error: it comes back as a status so
the dashboard can shout about it instead of silently showing zeros.

  python -m studio.metrics          # snapshot now, print the summary
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}

# The ops console polls; a snapshot a minute is plenty and keeps the metrics
# table from bloating with identical rows.
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


def persona_bluesky_handle() -> str:
    if os.environ.get("BLUESKY_HANDLE"):
        return os.environ["BLUESKY_HANDLE"]
    try:
        with open(ROOT / "config" / "persona.yaml") as f:
            return (yaml.safe_load(f).get("identity") or {}).get("handle") or ""
    except Exception:
        return ""


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
    if not handle:
        out["status"] = "unconfigured"
        return out
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


def fetch_telegram(channel: str) -> dict:
    out = {"platform": "telegram", "handle": channel, "status": "ok",
           "followers": None, "posts": []}
    name = channel.lstrip("@")
    if not name:
        out["status"] = "unconfigured"
        return out
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getChatMemberCount",
                          params={"chat_id": f"@{name}"}, timeout=15)
            if r.status_code == 200 and r.json().get("ok"):
                out["followers"] = r.json()["result"]
        except Exception:
            pass
    try:
        r = httpx.get(f"https://t.me/s/{name}", headers=UA, timeout=15,
                      follow_redirects=True)
        r.raise_for_status()
        out["posts"] = parse_tme(r.text)
    except Exception as e:
        out["status"] = f"error {str(e)[:60]}"
    return out


# ── snapshot ────────────────────────────────────────────────────

def collect(con=None, force: bool = False) -> dict:
    """Fetch every configured platform; optionally persist a snapshot.
    Cached for CACHE_TTL so dashboard polling doesn't hammer the sources."""
    if not force and _cache["data"] and time.time() - _cache["t"] < CACHE_TTL:
        return _cache["data"]
    platforms = [
        fetch_bluesky(persona_bluesky_handle()),
        fetch_telegram(os.environ.get("TELEGRAM_CHANNEL", "")),
    ]
    data = {"platforms": platforms, "captured_at": _now()}
    _cache.update(t=time.time(), data=data)
    if con is not None:
        rows = []
        for pl in platforms:
            if pl["status"] != "ok":
                continue
            rows.append((pl["platform"], "account", pl["handle"], pl["followers"],
                         None, None, None, None, data["captured_at"]))
            rows.extend((pl["platform"], "post", p["ref"], None, p["views"],
                         p["likes"], p["reposts"], p["replies"], data["captured_at"])
                        for p in pl["posts"])
        if rows:
            from studio import store
            store.save_metrics(con, rows)
    return data


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from studio import store

    data = collect(store.connect(), force=True)
    for pl in data["platforms"]:
        head = f"── {pl['platform']} ({pl['handle'] or 'unconfigured'})"
        if pl["status"] != "ok":
            print(f"{head} — {pl['status'].upper()}")
            continue
        print(f"{head} — {pl['followers']} followers · {len(pl['posts'])} posts")
        for p in pl["posts"][:8]:
            eng = (f"{p['views']} views" if p["views"] is not None
                   else f"{p['likes']}♥ {p['reposts']}↻ {p['replies']}💬")
            print(f"   {p['created_at']:<16} {eng:<18} {p['url']}")
