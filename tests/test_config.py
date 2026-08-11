"""Offline tests — no network, no secrets. These are what CI can actually prove.

They guard the contracts that break silently: config shape, the disclosure gate,
and the guardrail arithmetic.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config"


def test_every_persona_has_a_disclosure():
    """The disclosure line is the one invariant that must always exist — for
    every persona, not just the first one. Shape is covered in test_persona.py;
    this keeps the invariant asserted next to the other publish-gate tests."""
    from studio import persona

    assert persona.available()
    for pid in persona.available():
        p = persona.load(pid)
        assert p["identity"]["name"]
        assert p["identity"]["post_disclosure"].strip()
        assert p["voice"]["never_says"]
        assert p["visual_grammar"]["style_suffix"].strip()


def test_every_platform_policy_has_required_keys():
    policy = yaml.safe_load((CONFIG / "platform_policy.yaml").read_text())
    for name, cfg in policy.items():
        if name == "fleet":
            continue
        for key in ("hard_max_posts_per_day", "min_gap_hours", "warmup"):
            assert key in cfg, f"{name} missing {key}"
        assert cfg["warmup"], f"{name} has an empty warm-up curve"
        # the curve must terminate in an always-matching stage
        assert cfg["warmup"][-1]["days"] >= 9999, f"{name} warm-up never terminates"


def test_platform_formats_declare_fields_and_limits():
    fmts = yaml.safe_load((CONFIG / "platform_formats.yaml").read_text())
    for name, f in fmts.items():
        assert f["fields"], f"{name} declares no fields"
        if "title" in f["fields"]:
            assert f["title_limit"] and f["description_limit"]
        else:
            assert f["text_limit"], f"{name} has no text_limit"


def test_policy_and_formats_cover_the_same_platforms():
    policy = {k for k in yaml.safe_load((CONFIG / "platform_policy.yaml").read_text())
              if k != "fleet"}
    fmts = set(yaml.safe_load((CONFIG / "platform_formats.yaml").read_text()))
    # a platform we can publish to must have BOTH a safety policy and a format
    missing_format = policy - fmts
    assert not missing_format, f"policy without format: {missing_format}"


def test_disclosure_follows_provenance():
    """A stock photo must never be described as AI-generated."""
    from studio.publisher import disclosure_for

    generated = disclosure_for({"model": "fal-ai/z-image/turbo"})
    stock = disclosure_for({"model": "pexels:Jane Doe",
                            "credit": {"photographer": "Jane Doe"}})
    assert "AI-generated" in generated
    assert "Jane Doe" in stock and "Pexels" in stock
    assert "AI-generated" not in stock


def test_compose_never_truncates_the_disclosure():
    from studio.publisher import compose_plain, disclosure_for

    disclosure = disclosure_for(None)
    long_caption = "x" * 5000
    out = compose_plain(long_caption, limit=300)
    assert out.endswith(disclosure)
    assert len(out) <= 300


@pytest.mark.parametrize("age_days,expected", [(0.5, 0), (3, 1), (30, 2)])
def test_bluesky_warmup_curve(age_days, expected):
    from studio.guard import load_policy, warmup_cap

    assert warmup_cap(load_policy("bluesky"), age_days) == expected


def test_suspended_account_cannot_be_published_to(tmp_path, monkeypatch):
    """The costliest failure mode in this project is losing a grown account.
    A registry status other than active must outrank credentials, warm-up and
    cadence — posting into a takedown is what moderation reads as evasion."""
    from studio import guard, store

    monkeypatch.setattr(guard, "registry_account",
                        lambda platform: {"platform": platform, "status": "suspended",
                                          "opened_at": "2020-01-01"})
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    con = store.connect()
    ok, reason = guard.can_post(con, "bluesky")
    assert ok is False and "suspended" in reason


def test_warmup_clock_survives_a_machine_change(tmp_path, monkeypatch):
    """Regression: account age came from a machine-local DB row, so a fresh
    clone made a long-warmed account look newborn. The registry date is the
    source of truth; an unknown date must fail safe to age 0 (silent)."""
    from studio import guard, store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    con = store.connect()  # empty DB — no personas row at all

    monkeypatch.setattr(guard, "registry_account",
                        lambda platform: {"platform": platform, "status": "active",
                                          "opened_at": "2026-01-01"})
    assert guard._account_age_days(con, "bluesky") > 100

    monkeypatch.setattr(guard, "registry_account",
                        lambda platform: {"platform": platform, "status": "active"})
    assert guard._account_age_days(con, "bluesky") == 0.0


def _guard_con(tmp_path, monkeypatch, platform="telegram"):
    from studio import guard, store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(guard, "registry_account",
                        lambda p: {"platform": p, "status": "active",
                                   "handle": "chan", "opened_at": "2020-01-01"})
    return store.connect()


def test_cadence_counts_the_platform_when_the_database_was_reset(tmp_path, monkeypatch):
    """The database is machine-local: after a laptop change it reports zero
    posts today and the cap resets, so the account could be posted to twice
    its limit in one day. The platform's own feed cannot drift that way."""
    from studio import guard

    con = _guard_con(tmp_path, monkeypatch)          # empty DB — "no posts today"
    today = datetime.now(UTC).date().isoformat()
    monkeypatch.setattr(guard, "platform_activity",
                        lambda p, h: (4, f"{today}T09:00"))

    ok, reason = guard.can_post(con, "telegram")     # telegram cap is 4/day
    assert ok is False
    assert "cadence cap reached" in reason and "per platform" in reason


def test_an_unreachable_platform_is_not_permission(tmp_path, monkeypatch):
    """A failed fetch returns zero, which must never read as 'nothing posted
    today' — local history still applies."""
    from studio import guard, store

    con = _guard_con(tmp_path, monkeypatch)
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    for _ in range(4):
        store.save_post(con, 1, "telegram", "u", "url", "text", "published")

    ok, reason = guard.can_post(con, "telegram")
    assert ok is False and "cadence cap reached" in reason


def test_min_gap_uses_the_most_recent_of_both_sources(tmp_path, monkeypatch):
    from studio import guard

    con = _guard_con(tmp_path, monkeypatch)
    recent = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (1, recent))

    ok, reason = guard.can_post(con, "telegram")     # telegram min gap is 2h
    assert ok is False and "min-gap" in reason


def test_login_budget_stops_a_hammering_loop(tmp_path, monkeypatch):
    """Bluesky rate-limits createSession far below posting, and repeated
    authentication is a documented suspension trigger. One login a day is
    healthy; a loop must stop loudly instead of hammering the endpoint."""
    import json
    import time

    from studio import publisher

    log = tmp_path / "logins.json"
    monkeypatch.setattr(publisher, "LOGIN_LOG", log)
    monkeypatch.setattr(publisher, "STORE_DIR", tmp_path)

    log.write_text(json.dumps([time.time() - 7200]))       # one, two hours ago
    assert len(publisher._login_budget_check()) == 1        # still allowed

    log.write_text(json.dumps([time.time() - 60] * publisher.MAX_LOGINS_PER_HOUR))
    with pytest.raises(RuntimeError, match="login budget exhausted"):
        publisher._login_budget_check()

    # a day-old burst must age out rather than block forever
    log.write_text(json.dumps([time.time() - 90_000] * 50))
    assert publisher._login_budget_check() == []


def test_telegram_has_no_warmup_silence():
    """Telegram bots are sanctioned automation — a silent period would be
    protecting against a risk that does not exist there."""
    from studio.guard import load_policy, warmup_cap

    assert warmup_cap(load_policy("telegram"), 0.0) > 0


def test_every_category_config_is_complete():
    """A category must declare enough for the collector to actually query it."""
    import yaml as _yaml
    categories = sorted((ROOT / "config" / "categories").glob("*.yaml"))
    assert categories, "no category configs found"
    for path in categories:
        cfg = _yaml.safe_load(path.read_text())
        assert cfg.get("label"), f"{path.name} has no label"
        assert cfg.get("priority"), f"{path.name} has no priority"
        assert (cfg.get("reddit") or {}).get("subreddits"), f"{path.name}: no subreddits"
        assert cfg.get("news_queries"), f"{path.name}: no news queries"
        assert cfg.get("visual_keywords"), f"{path.name}: no visual keywords"


def test_category_keywords_gate_off_topic_items():
    """The relevance gate guards the two broad-catchment sources (country-wide
    trending searches, a tech forum). Topical sources — subreddits, news queries,
    trade RSS, chosen YouTube channels — are on-topic by construction and are
    never gated, so this gate is tuned for PRECISION: with ~200 items per
    category, dropping a borderline item costs nothing, while admitting noise
    pollutes every downstream scoring decision."""
    from studio.collector import _relevant, category_keywords, load_category

    kw = category_keywords(load_category("food-drink"))
    # clearly in-category items pass
    assert _relevant("The best restaurant openings of August", kw)
    assert _relevant("Show HN: CheapFoodMap, good meals under $10", kw)
    # real noise observed in production runs must not pass
    assert not _relevant("birthright citizenship ruling", kw)
    assert not _relevant("Launch HN: ProvenMetal delivers circuit boards", kw)
    assert not _relevant("The AI slowdown is coming", kw)


def test_generic_words_cannot_carry_the_gate():
    """Regression: 'home', 'science' and 'trend' appear in nearly every config
    and once matched almost any headline, silently disabling the gate."""
    from studio.collector import GENERIC, category_keywords, load_category

    for name in ("food-drink", "travel-places", "home-interiors"):
        assert not (category_keywords(load_category(name)) & GENERIC)


def test_taxonomy_and_configs_agree():
    """categories.yaml is the reasoning; config/categories/*.yaml are the sources.
    A category described in one but missing from the other is a silent gap."""
    import yaml as _yaml
    taxonomy = set(_yaml.safe_load(
        (ROOT / "config" / "categories.yaml").read_text())["categories"])
    on_disk = {p.stem for p in (ROOT / "config" / "categories").glob("*.yaml")}
    assert taxonomy == on_disk, f"mismatch: {taxonomy ^ on_disk}"


def test_every_persona_category_exists():
    from studio import persona
    from studio.collector import available_categories

    for pid in persona.available():
        category = persona.category_of(pid)
        assert category in available_categories(), \
            f"persona '{pid}' draws from '{category}', which has no config"
