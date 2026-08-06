#!/usr/bin/env python
"""Seed the personas table with a synthetic 100-account fleet so the fleet
view can be designed and demoed at real scale BEFORE real accounts exist.

Every seeded row has demo=1 — clearly marked in the DB and on the dashboard,
wiped with one command. No real accounts, credentials or API calls involved.

  python dashboard/seed_demo_fleet.py          # seed ~99 demo personas
  python dashboard/seed_demo_fleet.py --wipe   # remove all demo rows
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import store  # noqa: E402

FIRST = ["mara", "juno", "iris", "nova", "wren", "sage", "lila", "remy", "kai",
         "noor", "vera", "esme", "romy", "isla", "faye", "nico", "ada", "zoe",
         "mira", "luca", "tess", "enzo", "cleo", "aria", "otto"]
SECOND = ["brews", "roams", "lifts", "plates", "frames", "threads", "sketches",
          "rides", "bakes", "hikes", "spins", "reads", "grows", "swims"]
NICHES = {
    "coffee & city": 22, "travel & places": 18, "fitness & wellness": 15,
    "food & recipes": 14, "fashion & street": 10, "art & design": 8,
    "books & quiet life": 7, "plants & home": 6,
}
PLATFORMS = {"bluesky": 34, "instagram": 26, "tiktok": 18, "x": 14, "youtube": 8}
SUFFIX = {"bluesky": ".bsky.social", "instagram": "", "tiktok": "", "x": "", "youtube": ""}


def weighted(d: dict) -> list:
    return [k for k, w in d.items() for _ in range(w)]


def seed(n: int = 99):
    rng = random.Random(42)   # stable demo data run-to-run
    con = store.connect()
    existing = con.execute("SELECT COUNT(*) FROM personas WHERE demo=1").fetchone()[0]
    if existing:
        print(f"{existing} demo personas already present — wiping first")
        con.execute("DELETE FROM personas WHERE demo=1")
        con.commit()

    niches, plats = weighted(NICHES), weighted(PLATFORMS)
    now = datetime.now(UTC)
    handles = set()
    made = 0
    while made < n:
        name = rng.choice(FIRST)
        handle = f"{name}{rng.choice(SECOND)}{rng.choice(['', '', str(rng.randint(2, 99))])}"
        if handle in handles:
            continue
        handles.add(handle)
        platform = rng.choice(plats)
        # realistic status mix: mostly healthy, a visible handful of exceptions
        r = rng.random()
        if r < 0.78:
            status = "active"
        elif r < 0.88:
            status = "warming"
        elif r < 0.94:
            status = "paused"
        else:
            status = "suspended"
        posts_today = 0 if status in ("paused", "suspended") else rng.choice([0, 1, 1, 2, 2, 3])
        hours_ago = (rng.uniform(30, 200) if status in ("paused", "suspended")
                     else rng.uniform(0.2, 30))
        last_cycle = ("failed" if status == "suspended" or rng.random() < 0.06
                      else "published")
        con.execute(
            "INSERT INTO personas (name, handle, platform, niche, status, cadence,"
            " demo, posts_today, last_post_at, last_cycle_status, created_at)"
            " VALUES (?,?,?,?,?,?,1,?,?,?,?)",
            (name.capitalize(), handle + SUFFIX[platform], platform,
             rng.choice(niches), status, f"{rng.choice([1, 2, 2, 3])}/day",
             posts_today, (now - timedelta(hours=hours_ago)).isoformat(),
             last_cycle, (now - timedelta(days=rng.randint(3, 200))).isoformat()))
        made += 1
    con.commit()
    print(f"seeded {made} demo personas (demo=1) — wipe anytime with --wipe")


def wipe():
    con = store.connect()
    n = con.execute("SELECT COUNT(*) FROM personas WHERE demo=1").fetchone()[0]
    con.execute("DELETE FROM personas WHERE demo=1")
    con.commit()
    print(f"removed {n} demo personas")


if __name__ == "__main__":
    wipe() if "--wipe" in sys.argv else seed()
