"""Metrics parser tests — offline, the pure halves of the feedback loop.

Network fetch stays untested (CI is offline); what must never drift silently
is the parsing: t.me view counters, the message-block pairing, and the
Bluesky feed→row mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.metrics import _num, map_bluesky_feed, parse_tme  # noqa: E402


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
