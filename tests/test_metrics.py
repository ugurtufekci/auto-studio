"""Metrics tests — offline, the pure halves of the feedback loop.

Network fetch stays untested (CI is offline); what must never drift silently
is the parsing (t.me counters, message-block pairing, Bluesky feed mapping),
the fleet-registry contract, and the git-backed ledger round-trip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.metrics import (  # noqa: E402
    FETCHERS,
    _num,
    fleet_accounts,
    map_bluesky_feed,
    parse_tme,
    parse_tme_subscribers,
    persist_pool,
    read_history,
)


def test_view_counter_suffixes():
    assert _num("2") == 2
    assert _num("1.2K") == 1200
    assert _num("3M") == 3_000_000
    assert _num("12,345") == 12345


TME_SAMPLE = """
<div class="tgme_widget_message" data-post="marabrews/1">
  <div class="service">channel created — no view counter on service messages</div>
</div>
<div class="tgme_widget_message" data-post="marabrews/2">
  <time datetime="2026-08-07T09:15:00+00:00"></time>
  <span class="tgme_widget_message_views">2</span>
</div>
<div class="tgme_widget_message" data-post="marabrews/3">
  <time datetime="2026-08-08T18:00:00+00:00"></time>
  <span class="tgme_widget_message_views">1.4K</span>
</div>
"""


def test_tme_parsing_pairs_views_within_their_message_block():
    posts = parse_tme(TME_SAMPLE)
    # the service message (no counter) is skipped, not mis-paired
    assert [p["ref"] for p in posts] == ["marabrews/2", "marabrews/3"]
    assert posts[0]["views"] == 2 and posts[1]["views"] == 1400
    assert posts[0]["url"] == "https://t.me/marabrews/2"
    assert posts[0]["created_at"] == "2026-08-07T09:15"


def test_bluesky_feed_maps_to_engagement_rows():
    feed = {"feed": [{"post": {
        "uri": "at://did:plc:abc/app.bsky.feed.post/3xyz",
        "likeCount": 7, "repostCount": 2, "replyCount": 1,
        "record": {"createdAt": "2026-08-01T06:30:00.000Z"},
    }}]}
    rows = map_bluesky_feed(feed, "mara.bsky.social")
    assert rows[0]["url"] == "https://bsky.app/profile/mara.bsky.social/post/3xyz"
    assert (rows[0]["likes"], rows[0]["reposts"], rows[0]["replies"]) == (7, 2, 1)
    assert rows[0]["views"] is None  # bluesky exposes no view counts
    assert rows[0]["created_at"] == "2026-08-01T06:30"


def test_empty_feed_and_empty_page_are_fine():
    assert map_bluesky_feed({}, "x") == []
    assert parse_tme("<html>nothing here</html>") == []


def test_subscriber_count_parses_without_any_credential():
    """Regression: the cloud harvest has no bot token, and a token-only path
    logged followers=null into every ledger line — blinding the one metric
    that matters most. Both public markup shapes must parse."""
    preview = ('<div class="tgme_channel_info_counter">'
               '<span class="counter_value">2</span> '
               '<span class="counter_type">subscribers</span></div>')
    plain = '<div class="tgme_page_extra">1.4K subscribers</div>'
    assert parse_tme_subscribers(preview) == 2
    assert parse_tme_subscribers(plain) == 1400
    # a members counter on a group page must not be mistaken for subscribers
    assert parse_tme_subscribers('<div class="tgme_channel_info_counter">'
                                 '<span class="counter_value">9</span> '
                                 '<span class="counter_type">members</span></div>') is None
    assert parse_tme_subscribers("<html>nothing</html>") is None


def test_fleet_registry_contract():
    """config/accounts.yaml is the single source of truth for which accounts
    exist — every row must be measurable and name a real category pool."""
    rows = fleet_accounts()
    assert rows, "fleet registry is empty"
    categories = {p.stem for p in (ROOT / "config" / "categories").glob("*.yaml")}
    for r in rows:
        for key in ("persona", "platform", "handle", "category"):
            assert r.get(key), f"registry row missing '{key}': {r}"
        assert r["platform"] in FETCHERS, \
            f"no metrics fetcher for platform '{r['platform']}'"
        assert not r["handle"].startswith(("@", "http")), \
            f"handles are stored bare: {r['handle']}"
        assert r["category"] in categories, \
            f"registry names unknown category '{r['category']}'"


def _snapshot(ts: str, followers: int) -> dict:
    return {"captured_at": ts, "accounts": [{
        "persona": "mara", "platform": "telegram", "handle": "marabrews",
        "category": "food-drink", "status": "ok", "followers": followers,
        "posts": [{"ref": "marabrews/2", "url": "https://t.me/marabrews/2",
                   "views": 5, "likes": None, "reposts": None, "replies": None,
                   "created_at": "2026-08-06T15:30"}],
    }]}


def test_ledger_roundtrip_and_corruption_tolerance(tmp_path):
    persist_pool(_snapshot("2026-08-08T04:00:00+00:00", 2), base=tmp_path)
    persist_pool(_snapshot("2026-08-08T13:00:00+00:00", 4), base=tmp_path)
    acct_dir = tmp_path / "telegram--marabrews"

    latest = json.loads((acct_dir / "latest.json").read_text())
    assert latest["followers"] == 4 and latest["posts"][0]["views"] == 5

    # one corrupt line must never blind the whole trend
    with open(acct_dir / "history.jsonl", "a") as f:
        f.write("{corrupt\n")
    hist = read_history("telegram", "marabrews", base=tmp_path)
    assert [h["followers"] for h in hist] == [2, 4]  # oldest → newest
    assert hist[0]["views_total"] == 5 and hist[0]["status"] == "ok"


def test_suspended_account_still_enters_the_ledger(tmp_path):
    """Outage periods must stay visible in history, not vanish from it."""
    persist_pool({"captured_at": "2026-08-08T04:00:00+00:00", "accounts": [{
        "persona": "mara", "platform": "bluesky", "handle": "x.bsky.social",
        "category": "food-drink", "status": "suspended", "followers": None,
        "posts": []}]}, base=tmp_path)
    hist = read_history("bluesky", "x.bsky.social", base=tmp_path)
    assert hist[0]["status"] == "suspended" and hist[0]["followers"] is None
