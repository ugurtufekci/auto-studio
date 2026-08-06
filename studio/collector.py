"""Trend collector — handbook page 03 (Trend intelligence), miniature.

Four keyless-or-already-authed sources, each wrapped so one dead source never
kills a cycle (the handbook's "source rot" failure mode):

  - Bluesky native search   (platform trend surface — engagement velocity)
  - Reddit hot via .rss     (community forums; .json endpoints are bot-blocked)
  - Coffee-industry RSS     (culture press — confirms rather than predicts)
  - Google Trends rising    (pytrends; flakiest — fully optional)

Output: raw items {source, kind, title, detail, url, score_hint, age_hours, fetched_at}.
score_hint is a velocity proxy where the source provides one, else 0.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser
import httpx
import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_sources() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


# ── Bluesky native search ───────────────────────────────────────

def collect_bluesky(cfg: dict) -> list[dict]:
    """Top posts for niche keywords in the last N hours, via the PUBLIC AppView
    API — deliberately unauthenticated. Read traffic must never ride on the
    persona account (lesson learned: authed search bursts on an hours-old
    account pattern-match spam onboarding and get accounts suspended).
    Velocity = engagement/hour."""
    items = []
    bs = cfg.get("bluesky_search")
    if not bs:
        return items
    # 1 · platform trending board (public, reliable) — the niche gates in the
    # signal scorer discard the off-topic majority
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrends",
                      params={"limit": 15}, headers=UA, timeout=15)
        r.raise_for_status()
        for t in r.json().get("trends", []):
            started = t.get("startedAt", "")
            age_h = 1.0
            if started:
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                age_h = max((datetime.now(UTC) - dt).total_seconds() / 3600, 0.5)
            posts = t.get("postCount", 0)
            items.append({
                "source": "bsky:trending",
                "kind": "bluesky",
                "title": t.get("displayName") or t.get("topic", ""),
                "detail": f"platform trend · {posts} posts · {t.get('category', '?')}"
                          f" · status {t.get('status', '-')}",
                "url": f"https://bsky.app{t.get('link', '')}" if t.get("link") else "",
                "score_hint": round(posts / age_h, 1),
                "age_hours": round(age_h, 1),
                "fetched_at": _now(),
            })
    except Exception as e:
        print(f"  [collector] bsky trending unavailable: {str(e)[:80]}")

    # 2 · keyword search (edge-gated as of Aug 2026 — contributes when open)
    since_dt = datetime.now(UTC) - timedelta(hours=bs.get("since_hours", 24))
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    search_dead = False
    for kw in bs["keywords"]:
        if search_dead:
            break
        try:
            r = httpx.get(
                "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params={"q": kw, "sort": "top", "since": since,
                        "limit": bs.get("posts_per_keyword", 15)},
                headers=UA, timeout=15)
            if r.status_code == 403:
                print("  [collector] bsky keyword search edge-blocked — using trending board only")
                search_dead = True
                continue
            r.raise_for_status()
            for p in r.json().get("posts", []):
                rec = p.get("record", {})
                created = datetime.fromisoformat(
                    rec.get("createdAt", "").replace("Z", "+00:00"))
                age_h = max((datetime.now(UTC) - created).total_seconds() / 3600, 0.5)
                likes = p.get("likeCount", 0)
                reposts = p.get("repostCount", 0)
                handle = p.get("author", {}).get("handle", "")
                items.append({
                    "source": f"bsky:{kw}",
                    "kind": "bluesky",
                    "title": (rec.get("text") or "").replace("\n", " ")[:200],
                    "detail": f"by @{handle} · {likes} likes, {reposts} reposts",
                    "url": f"https://bsky.app/profile/{handle}/post/{p.get('uri', '').split('/')[-1]}",
                    "score_hint": round((likes + 2 * reposts) / age_h, 1),
                    "age_hours": round(age_h, 1),
                    "fetched_at": _now(),
                })
            time.sleep(1.0)
        except Exception as e:
            print(f"  [collector] bsky search '{kw}' failed: {str(e)[:80]}")
    return items


# ── Reddit (via RSS — json endpoints are bot-blocked) ───────────

def collect_reddit(cfg: dict) -> list[dict]:
    items = []
    for sub in cfg["reddit"]["subreddits"]:
        url = f"https://www.reddit.com/r/{sub}/hot.rss?limit={cfg['reddit']['posts_per_sub']}"
        try:
            r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
            if r.status_code == 429:            # backoff once, then give up quietly
                time.sleep(20)
                r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
            for entry in parsed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                age_h = (time.time() - time.mktime(published)) / 3600 if published else None
                items.append({
                    "source": f"r/{sub}",
                    "kind": "reddit",
                    "title": entry.get("title", ""),
                    "detail": "",
                    "url": entry.get("link", ""),
                    "score_hint": 0,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "fetched_at": _now(),
                })
            time.sleep(6)  # reddit rate-limits aggressively on burst requests
        except Exception as e:
            print(f"  [collector] r/{sub} failed: {e}")
    return items


# ── Industry RSS ────────────────────────────────────────────────

def collect_rss(cfg: dict) -> list[dict]:
    items = []
    for feed in cfg["rss"]["feeds"]:
        try:
            r = httpx.get(feed["url"], headers=UA, timeout=15, follow_redirects=True)
            parsed = feedparser.parse(r.text)
            if not parsed.entries:
                print(f"  [collector] RSS {feed['name']}: 0 entries (http {r.status_code})")
            for entry in parsed.entries[:8]:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                age_h = (time.time() - time.mktime(published)) / 3600 if published else None
                items.append({
                    "source": feed["name"],
                    "kind": "rss",
                    "title": entry.get("title", ""),
                    "detail": (entry.get("summary") or "")[:400],
                    "url": entry.get("link", ""),
                    "score_hint": 0,
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "fetched_at": _now(),
                })
        except Exception as e:
            print(f"  [collector] RSS {feed['name']} failed: {e}")
    return items


# ── Google Trends (optional, flaky by nature) ───────────────────

def collect_trends(cfg: dict) -> list[dict]:
    items = []
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0, timeout=(5, 15))
        kws = cfg["google_trends"]["seed_keywords"]
        for batch_start in range(0, len(kws), 5):  # pytrends max 5 per request
            batch = kws[batch_start:batch_start + 5]
            pt.build_payload(batch, geo=cfg["google_trends"].get("geo", ""),
                             timeframe=cfg["google_trends"].get("timeframe", "now 7-d"))
            related = pt.related_queries()
            for kw, tables in related.items():
                rising = tables.get("rising")
                if rising is None:
                    continue
                for _, row in rising.head(5).iterrows():
                    items.append({
                        "source": f"gtrends:{kw}",
                        "kind": "trends",
                        "title": str(row["query"]),
                        "detail": f"rising search related to '{kw}' (+{row['value']}%)",
                        "url": "",
                        "score_hint": float(row["value"]),
                        "age_hours": 0,
                        "fetched_at": _now(),
                    })
            time.sleep(2)
    except Exception as e:
        print(f"  [collector] Google Trends unavailable this cycle: {e}")
    return items


# ── entry point ─────────────────────────────────────────────────

def collect_all(on_progress=None) -> list[dict]:
    """All sources are unauthenticated — collection never touches the
    persona's account or credentials."""
    cfg = load_sources()
    items = []
    got = collect_bluesky(cfg)
    print(f"  [collector] bluesky: {len(got)} items")
    if on_progress:
        on_progress("bluesky search", len(got))
    items.extend(got)
    for fn, label in [(collect_reddit, "reddit"), (collect_rss, "rss"), (collect_trends, "trends")]:
        got = fn(cfg)
        print(f"  [collector] {label}: {len(got)} items")
        if on_progress:
            on_progress(label, len(got))
        items.extend(got)
    return items


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    all_items = collect_all()
    print(f"\ntotal: {len(all_items)} raw items")
    for it in sorted(all_items, key=lambda x: -(x["score_hint"] or 0))[:12]:
        print(f"  {it['score_hint']:>7} | {it['source']:<22} | {it['title'][:70]}")
