"""Shared signal pool reader — the consumer half of the trend-harvest routine.

The harvest (routines/trend-harvest.md) collects and judges trends for every
launch-priority category and commits the result to
data/signals/<category>/latest.json. This module is how a publishing cycle
draws on that pool. Collection is shared, publishing is per-account: one
harvest feeds every persona subscribed to a category, so the account whose
persona.yaml says food-drink reads the food-drink pool and the travel account
reads travel-places — nobody re-collects, nobody re-scores.

The pool schema is the harvest prompt's output contract
(routines/trend-harvest.prompt.md): signals carry `type` and `category_fit`.
The studio's internal schema (store.signals, brain, dashboard) says
`signal_type` and `niche_fit`. _adapt() bridges the two so neither side has to
change, and test_pool.py pins the committed pools against this contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

POOL_DIR = Path(__file__).resolve().parent.parent / "data" / "signals"

# The harvest lands twice a day, so a pool this old means several missed runs.
# Signals inside may still be within their own expiry (an aesthetic wave lives
# for weeks), so staleness is a loud warning to the operator, not a refusal.
STALE_AFTER_HOURS = 48.0


def available_pools(pool_dir: Path = POOL_DIR) -> list[str]:
    """Categories that currently have a pool on disk."""
    return sorted(p.parent.name for p in pool_dir.glob("*/latest.json"))


def _adapt(signal: dict, category: str) -> dict:
    """Pool record → internal signal record (the store/brain/dashboard shape).

    Defensive by design: the pool is written by a harvest agent following a
    prose spec, and agents follow specs literally. On 2026-08-21 a harvest
    shipped signals with no `topic` — the spec never explicitly required
    one — and every consumer from run.py to the dashboard indexes it. A
    missing name is synthesized from the summary rather than crashing the
    cycle that reads it."""
    s = dict(signal)
    if "type" in s:
        s["signal_type"] = s.pop("type")
    if "category_fit" in s:
        s["niche_fit"] = s.pop("category_fit")
    if not s.get("topic"):
        summary = str(s.get("summary") or "").strip()
        head = summary.split(".")[0].strip() if summary else category
        words = head.split()
        s["topic"] = (" ".join(words[:8]) + ("…" if len(words) > 8 else "")
                      if words else category)
    s.setdefault("signal_type", "topic")
    s.setdefault("score", 0.5)
    s.setdefault("source_count", 1)
    s.setdefault("exemplar_urls", [])
    s["category"] = category
    return s


def load_pool(category: str, pool_dir: Path = POOL_DIR) -> dict:
    path = pool_dir / category / "latest.json"
    if not path.exists():
        have = ", ".join(available_pools(pool_dir)) or "none"
        raise FileNotFoundError(
            f"no signal pool for '{category}' ({path}) — pools on disk: {have}. "
            "Run the trend-harvest routine, or fall back to run.py --live-collect.")
    return json.loads(path.read_text(encoding="utf-8"))


def read_signals(categories: list[str], now: datetime | None = None,
                 pool_dir: Path = POOL_DIR) -> tuple[list[dict], list[dict]]:
    """Fresh signals for one or more categories, best-first, plus per-pool meta.

    The harvest judged how long each wave stays postable (expiry_hours); the
    reader's job is to believe it — anything past harvested_at + expiry_hours
    is dropped here mechanically, so a paused harvest can never make an account
    post last week's wave as if it were news.
    """
    now = now or datetime.now(UTC)
    signals: list[dict] = []
    meta: list[dict] = []
    for category in categories:
        pool = load_pool(category, pool_dir)
        harvested = datetime.fromisoformat(pool["harvested_at"])
        if harvested.tzinfo is None:
            harvested = harvested.replace(tzinfo=UTC)
        age_hours = (now - harvested).total_seconds() / 3600
        kept, expired = [], 0
        for s in pool.get("signals") or []:
            if age_hours > float(s.get("expiry_hours") or 0):
                expired += 1
                continue
            kept.append(_adapt(s, category))
        signals.extend(kept)
        meta.append({
            "category": category,
            "harvested_at": pool["harvested_at"],
            "age_hours": round(age_hours, 1),
            "stale": age_hours > STALE_AFTER_HOURS,
            "raw_item_count": int(pool.get("raw_item_count") or 0),
            "kept": len(kept),
            "expired": expired,
        })
    signals.sort(key=lambda s: -(s.get("score") or 0))
    return signals, meta


if __name__ == "__main__":
    import sys

    picked = sys.argv[1:] or available_pools()
    sigs, pools = read_signals(picked)
    for p in pools:
        flag = " · STALE" if p["stale"] else ""
        print(f"── {p['category']} — harvested {p['age_hours']}h ago, "
              f"{p['kept']} fresh / {p['expired']} expired{flag}")
    print(f"\n{len(sigs)} postable signals:")
    for s in sigs:
        print(f"  {s['score']:.2f} [{s['signal_type']:<9}] {s['category']:<15} "
              f"{s['topic']}  (expires {s['expiry_hours']}h after harvest)")
