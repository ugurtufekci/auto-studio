"""Signal-pool reader tests — the harvest ↔ publisher contract.

The trend-harvest routine writes data/signals/<category>/latest.json; run.py
reads it. These pin the schema adapter (pool `type`/`category_fit` → internal
`signal_type`/`niche_fit`), the mechanical expiry gate, and — against the pools
actually committed in this repo — that both sides still agree.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import pool  # noqa: E402

# every column store.save_signals() inserts — a pool signal must adapt to this
STORE_KEYS = ("topic", "signal_type", "summary", "why_now", "velocity",
              "niche_fit", "producibility", "expiry_hours", "score")

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _signal(**over) -> dict:
    s = {"topic": "Home baking surge", "type": "topic",
         "summary": "Layer cakes everywhere.", "why_now": "Ten posts in 13h.",
         "velocity": 0.8, "category_fit": 1.0, "producibility": 0.95,
         "score": 0.918, "expiry_hours": 336, "source_count": 10,
         "exemplar_urls": ["https://example.com/a"]}
    s.update(over)
    return s


def _write_pool(pool_dir: Path, category: str, signals: list[dict],
                harvested_at: str | None = None, raw: int = 185) -> None:
    d = pool_dir / category
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps({
        "category": category,
        "harvested_at": harvested_at or (NOW - timedelta(hours=2)).isoformat(),
        "raw_item_count": raw,
        "sources": {"reddit": 50},
        "signals": signals,
    }))


def test_pool_schema_adapts_to_store_schema(tmp_path):
    _write_pool(tmp_path, "food-drink", [_signal()])
    sigs, meta = pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    assert len(sigs) == 1
    s = sigs[0]
    for key in STORE_KEYS:
        assert key in s, f"adapted signal missing store column '{key}'"
    assert s["signal_type"] == "topic" and "type" not in s
    assert s["niche_fit"] == 1.0 and "category_fit" not in s
    assert s["category"] == "food-drink"
    assert meta[0]["kept"] == 1 and meta[0]["raw_item_count"] == 185


def test_expired_signals_are_dropped_mechanically(tmp_path):
    _write_pool(tmp_path, "food-drink",
                [_signal(topic="dead meme", expiry_hours=24, score=0.99),
                 _signal(topic="long wave", expiry_hours=72, score=0.5)],
                harvested_at=(NOW - timedelta(hours=30)).isoformat())
    sigs, meta = pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    assert [s["topic"] for s in sigs] == ["long wave"]
    assert meta[0]["expired"] == 1 and meta[0]["kept"] == 1


def test_stale_pool_is_flagged_but_still_served(tmp_path):
    _write_pool(tmp_path, "food-drink", [_signal(expiry_hours=1000)],
                harvested_at=(NOW - timedelta(hours=100)).isoformat())
    sigs, meta = pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    assert meta[0]["stale"] is True
    assert len(sigs) == 1  # within its own expiry — the operator decides, not us


def test_naive_harvested_at_is_treated_as_utc(tmp_path):
    _write_pool(tmp_path, "food-drink", [_signal()],
                harvested_at=(NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat())
    _, meta = pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    assert meta[0]["age_hours"] == 2.0


def test_missing_pool_names_the_alternatives(tmp_path):
    _write_pool(tmp_path, "travel-places", [_signal()])
    with pytest.raises(FileNotFoundError) as e:
        pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    assert "travel-places" in str(e.value)      # what IS on disk
    assert "--live-collect" in str(e.value)     # the escape hatch


def test_accounts_on_different_categories_draw_different_signals(tmp_path):
    """The architecture in one test: collection shared, publishing per-account."""
    _write_pool(tmp_path, "food-drink", [_signal(topic="smashburgers", score=0.9)])
    _write_pool(tmp_path, "travel-places",
                [_signal(topic="national parks", score=0.95),
                 _signal(topic="sleeper trains", score=0.7)])
    account_a, _ = pool.read_signals(["food-drink"], now=NOW, pool_dir=tmp_path)
    account_b, _ = pool.read_signals(["travel-places"], now=NOW, pool_dir=tmp_path)
    assert [s["topic"] for s in account_a] == ["smashburgers"]
    assert [s["topic"] for s in account_b] == ["national parks", "sleeper trains"]
    # a multi-category read merges best-first
    both, _ = pool.read_signals(["food-drink", "travel-places"],
                                now=NOW, pool_dir=tmp_path)
    assert [s["topic"] for s in both] == ["national parks", "smashburgers",
                                          "sleeper trains"]


def test_committed_pools_still_satisfy_the_contract():
    """The pools the harvest actually committed must adapt cleanly — this is
    the tripwire if either side of the contract drifts."""
    categories = pool.available_pools()
    assert categories, "no pools committed under data/signals/"
    for category in categories:
        data = pool.load_pool(category)
        harvested = datetime.fromisoformat(data["harvested_at"])
        # read at harvest time so nothing has expired yet
        sigs, meta = pool.read_signals([category], now=harvested)
        assert len(sigs) == len(data["signals"]), f"{category}: signals lost"
        for s in sigs:
            for key in STORE_KEYS:
                assert key in s, f"{category}: signal missing '{key}'"
            assert isinstance(s["exemplar_urls"], list)
            assert 0.0 <= s["score"] <= 1.0
