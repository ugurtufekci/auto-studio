"""Trend collector — handbook page 03 (Trend intelligence).

Six sources, all unauthenticated, all verified working. Nothing here touches a
persona's credentials: collection must never be able to get an account flagged.

  reddit_combined   r/a+b+c/hot.rss — ONE request per niche, ~50 items.
                    (Per-subreddit requests hit 429 constantly; combining the
                    subreddits into a single feed removed the whole problem.)
  google_news       news.google.com/rss/search?q=… — ~100 items per query,
                    any topic, any language. The main breadth lever.
  google_trends     trends.google.com/trending/rss — daily trending searches.
                    (Replaces pytrends, which returned 429 on every attempt.)
  youtube_channels  youtube.com/feeds/videos.xml — what creators in the niche
                    are actually publishing. Signal from a platform we post to.
  industry_rss      niche trade press — confirms a wave rather than predicting it.
  hacker_news       Algolia search, carries points/comments as engagement.

Each source is wrapped so one dead source degrades a cycle instead of killing it.
Every item is normalised to one record shape and tagged with its niche.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import feedparser
import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
NICHE_DIR = CONFIG_DIR / "niches"
CACHE_FILE = ROOT / "store" / "youtube_channels.json"

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _age_hours(struct_time) -> float | None:
    if not struct_time:
        return None
    return round((time.time() - time.mktime(struct_time)) / 3600, 1)


def load_global() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)


def available_niches() -> list[str]:
    return sorted(p.stem for p in NICHE_DIR.glob("*.yaml"))


def load_niche(niche: str) -> dict:
    path = NICHE_DIR / f"{niche}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"unknown niche '{niche}' — available: {', '.join(available_niches())}")
    with open(path) as f:
        return yaml.safe_load(f)


def niche_keywords(cfg: dict) -> set[str]:
    """Words that mark an item as belonging to this niche. Built from the
    niche's own config so adding a niche needs no code change."""
    words: set[str] = set()
    for field in ("news_queries", "visual_keywords", "hn_queries"):
        for phrase in cfg.get(field) or []:
            words.update(w for w in re.split(r"\W+", phrase.lower()) if len(w) > 3)
    for sub in (cfg.get("reddit") or {}).get("subreddits") or []:
        words.add(sub.lower())
    words.update(w for w in re.split(r"\W+", (cfg.get("name") or "").lower())
                 if len(w) > 3)
    return words


def _relevant(title: str, keywords: set[str]) -> bool:
    """Broad-catchment sources (country-wide trending searches, a tech forum)
    return mostly off-niche noise. Requiring one niche word keeps the pool
    honest — an empty result from these sources is the correct result."""
    text = title.lower()
    return any(k in text for k in keywords)


def _item(niche: str, source: str, kind: str, title: str, *, detail: str = "",
          url: str = "", score_hint: float = 0.0,
          age_hours: float | None = None) -> dict:
    return {"niche": niche, "source": source, "kind": kind,
            "title": (title or "").replace("\n", " ").strip()[:250],
            "detail": (detail or "")[:400], "url": url or "",
            "score_hint": round(score_hint, 1), "age_hours": age_hours,
            "fetched_at": _now()}


# ── reddit: one combined feed per niche ─────────────────────────

def collect_reddit(niche: str, cfg: dict) -> list[dict]:
    rc = cfg.get("reddit") or {}
    subs = rc.get("subreddits") or []
    if not subs:
        return []
    combined = "+".join(subs)
    url = f"https://www.reddit.com/r/{combined}/hot.rss"
    r = httpx.get(url, params={"limit": rc.get("limit", 50)},
                  headers=UA, timeout=25, follow_redirects=True)
    if r.status_code == 429:
        time.sleep(15)
        r = httpx.get(url, params={"limit": rc.get("limit", 50)},
                      headers=UA, timeout=25, follow_redirects=True)
    r.raise_for_status()
    parsed = feedparser.parse(r.text)
    out = []
    for e in parsed.entries:
        # the feed encodes the origin subreddit in the entry's category/link
        sub = ""
        m = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)/", e.get("link", ""))
        if m:
            sub = m.group(1)
        out.append(_item(niche, f"r/{sub or combined.split('+')[0]}", "reddit",
                         e.get("title", ""), url=e.get("link", ""),
                         age_hours=_age_hours(e.get("published_parsed")
                                              or e.get("updated_parsed"))))
    return out


# ── google news: query-driven breadth ───────────────────────────

def collect_google_news(niche: str, cfg: dict, per_query: int = 20) -> list[dict]:
    out = []
    for q in cfg.get("news_queries") or []:
        try:
            r = httpx.get("https://news.google.com/rss/search",
                          params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                          headers=UA, timeout=25, follow_redirects=True)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
            for e in parsed.entries[:per_query]:
                src = (e.get("source", {}) or {}).get("title", "") if isinstance(
                    e.get("source"), dict) else ""
                out.append(_item(niche, f"news:{q}", "news", e.get("title", ""),
                                 detail=src, url=e.get("link", ""),
                                 age_hours=_age_hours(e.get("published_parsed"))))
            time.sleep(0.6)
        except Exception as e:
            print(f"  [collector] news '{q}' failed: {str(e)[:70]}")
    return out


# ── google trends: daily trending searches ──────────────────────

def collect_google_trends(niche: str, geo: str = "US",
                          keywords: set[str] | None = None) -> list[dict]:
    r = httpx.get("https://trends.google.com/trending/rss",
                  params={"geo": geo}, headers=UA, timeout=25, follow_redirects=True)
    r.raise_for_status()
    parsed = feedparser.parse(r.text)
    out = []
    dropped = 0
    for e in parsed.entries:
        if keywords and not _relevant(e.get("title", ""), keywords):
            dropped += 1
            continue
        traffic = 0.0
        raw = str(e.get("ht_approx_traffic") or e.get("approx_traffic") or "")
        m = re.search(r"([\d,]+)", raw)
        if m:
            traffic = float(m.group(1).replace(",", ""))
        out.append(_item(niche, f"gtrends:{geo}", "trends", e.get("title", ""),
                         detail=f"daily trending search · ~{int(traffic)}+ searches"
                                if traffic else "daily trending search",
                         url=e.get("link", ""), score_hint=min(traffic / 1000, 5.0),
                         age_hours=_age_hours(e.get("published_parsed"))))
    if dropped:
        print(f"  [collector] trends: dropped {dropped} off-niche trending terms")
    return out


# ── youtube: what creators in the niche publish ─────────────────

def _channel_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def resolve_channel_id(handle: str) -> str | None:
    """@handle → UC… channel id, cached on disk (the RSS feed needs the id)."""
    cache = _channel_cache()
    if handle in cache:
        return cache[handle]
    r = httpx.get(f"https://www.youtube.com/@{handle}", headers=UA,
                  timeout=25, follow_redirects=True)
    m = (re.search(r'"channelId":"(UC[\w-]{20,})"', r.text)
         or re.search(r"channel/(UC[\w-]{20,})", r.text))
    if not m:
        return None
    cache[handle] = m.group(1)
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))
    return cache[handle]


def collect_youtube(niche: str, cfg: dict, per_channel: int = 6) -> list[dict]:
    out = []
    for handle in cfg.get("youtube_channels") or []:
        try:
            cid = resolve_channel_id(handle)
            if not cid:
                print(f"  [collector] youtube @{handle}: channel id not found")
                continue
            r = httpx.get("https://www.youtube.com/feeds/videos.xml",
                          params={"channel_id": cid}, headers=UA, timeout=25)
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
            for e in parsed.entries[:per_channel]:
                out.append(_item(niche, f"yt:{handle}", "youtube",
                                 e.get("title", ""),
                                 detail=f"video by {handle}",
                                 url=e.get("link", ""),
                                 age_hours=_age_hours(e.get("published_parsed"))))
            time.sleep(0.4)
        except Exception as e:
            print(f"  [collector] youtube @{handle} failed: {str(e)[:70]}")
    return out


# ── industry rss ────────────────────────────────────────────────

def collect_rss(niche: str, cfg: dict, per_feed: int = 8) -> list[dict]:
    out = []
    for feed in cfg.get("rss_feeds") or []:
        try:
            r = httpx.get(feed["url"], headers=UA, timeout=25, follow_redirects=True)
            parsed = feedparser.parse(r.text)
            if not parsed.entries:
                print(f"  [collector] rss {feed['name']}: 0 entries "
                      f"(http {r.status_code})")
            for e in parsed.entries[:per_feed]:
                out.append(_item(niche, feed["name"], "rss", e.get("title", ""),
                                 detail=(e.get("summary") or "")[:300],
                                 url=e.get("link", ""),
                                 age_hours=_age_hours(e.get("published_parsed")
                                                      or e.get("updated_parsed"))))
        except Exception as e:
            print(f"  [collector] rss {feed['name']} failed: {str(e)[:70]}")
    return out


# ── hacker news: carries engagement ─────────────────────────────

def collect_hn(niche: str, cfg: dict, per_query: int = 15,
               keywords: set[str] | None = None) -> list[dict]:
    out = []
    dropped = 0
    for q in cfg.get("hn_queries") or []:
        try:
            r = httpx.get("https://hn.algolia.com/api/v1/search_by_date",
                          params={"query": q, "tags": "story",
                                  "hitsPerPage": per_query}, timeout=25)
            r.raise_for_status()
            for h in r.json().get("hits", []):
                if keywords and not _relevant(h.get("title") or "", keywords):
                    dropped += 1
                    continue
                points = h.get("points") or 0
                created = h.get("created_at_i")
                age = round(max((time.time() - created) / 3600, 0.5), 1) if created else None
                out.append(_item(niche, f"hn:{q}", "hn", h.get("title") or "",
                                 detail=f"{points} points, "
                                        f"{h.get('num_comments') or 0} comments",
                                 url=h.get("url") or
                                     f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                                 score_hint=points / age if age else points,
                                 age_hours=age))
            time.sleep(0.3)
        except Exception as e:
            print(f"  [collector] hn '{q}' failed: {str(e)[:70]}")
    if dropped:
        print(f"  [collector] hn: dropped {dropped} off-niche stories")
    return out


# ── entry point ─────────────────────────────────────────────────

SOURCES = [
    ("reddit", lambda n, c, g, kw: collect_reddit(n, c)),
    ("google news", lambda n, c, g, kw: collect_google_news(n, c)),
    ("google trends", lambda n, c, g, kw: collect_google_trends(
        n, (g.get("google_trends") or {}).get("geo") or "US", kw)),
    ("youtube", lambda n, c, g, kw: collect_youtube(n, c)),
    ("industry rss", lambda n, c, g, kw: collect_rss(n, c)),
    ("hacker news", lambda n, c, g, kw: collect_hn(n, c, keywords=kw)),
]


def collect_niche(niche: str, on_progress=None) -> list[dict]:
    """Every source for one niche. Unauthenticated throughout."""
    cfg = load_niche(niche)
    glob = load_global()
    kw = niche_keywords(cfg)
    items: list[dict] = []
    for label, fn in SOURCES:
        try:
            got = fn(niche, cfg, glob, kw)
        except Exception as e:
            got = []
            print(f"  [collector] {label} unavailable: {str(e)[:80]}")
        print(f"  [collector] {label}: {len(got)} items")
        if on_progress:
            on_progress(f"{niche}/{label}", len(got))
        items.extend(got)
    return items


def collect_all(niches: list[str] | None = None, on_progress=None) -> list[dict]:
    """One or more niches in a single pass."""
    targets = niches or [available_niches()[0]]
    items = []
    for n in targets:
        items.extend(collect_niche(n, on_progress=on_progress))
    return items


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv
    load_dotenv()
    picked = sys.argv[1:] or available_niches()
    print(f"niches: {', '.join(picked)}\n")
    all_items = []
    for n in picked:
        print(f"── {n} ──")
        got = collect_niche(n)
        print(f"  → {len(got)} items\n")
        all_items.extend(got)
    print(f"TOTAL: {len(all_items)} raw items across {len(picked)} niches")
    by_kind: dict[str, int] = {}
    for it in all_items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
    print("by kind:", dict(sorted(by_kind.items(), key=lambda kv: -kv[1])))
    print("\ntop by velocity proxy:")
    for it in sorted(all_items, key=lambda x: -(x["score_hint"] or 0))[:10]:
        print(f"  {it['score_hint']:>7} | {it['niche']:<10} | {it['source']:<18} "
              f"| {it['title'][:58]}")
