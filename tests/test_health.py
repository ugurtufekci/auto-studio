"""Fleet-health tests — the semantics of the console's picture.

The one confusion this module exists to kill: a red/green dot per PLATFORM
cannot describe ACCOUNTS. One persona's Bluesky can be suspended while
another persona's is fine, so health is computed per registry account, the
operator inbox carries only human-actionable items, and machine keys are a
deployment fact rather than a health verdict. These tests pin exactly that.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import health, metrics, pool, store  # noqa: E402
from studio import publisher_instagram as ig  # noqa: E402

ALL_CRED_VARS = (
    "BLUESKY_APP_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL",
    "INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_HANDLE",
    "MASTODON_INSTANCE", "MASTODON_TOKEN", "MEDIA_HOST",
    "MEDIA_PUBLIC_BASE_URL", "MEDIA_LOCAL_DIR",
    "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in ALL_CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    # real machine state (token file, committed metrics ledger) must not leak in
    monkeypatch.setattr(ig, "TOKEN_FILE", Path("/nonexistent/tok.json"))
    monkeypatch.setattr(metrics, "METRICS_DIR", tmp_path / "ledger")


@pytest.fixture
def con(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    return store.connect()


def _registry(monkeypatch, tmp_path, accounts: list[dict]):
    reg = tmp_path / "accounts.yaml"
    reg.write_text(yaml.safe_dump({"accounts": accounts}))
    monkeypatch.setattr(metrics, "REGISTRY", reg)


def _days_ago(n: float) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat()


def _empty_pools(monkeypatch, tmp_path):
    d = tmp_path / "pools"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(pool, "POOL_DIR", d)
    return d


def _write_pool(pool_dir: Path, category: str, age_hours: float):
    d = pool_dir / category
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps({
        "category": category,
        "harvested_at": (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat(),
        "raw_item_count": 10,
        "signals": [{"topic": "t", "type": "topic", "summary": "s",
                     "why_now": "w", "velocity": .5, "category_fit": .9,
                     "producibility": .9, "score": .8, "expiry_hours": 400}]}))


# ── the core semantic: health per account, never per platform ───

def test_two_accounts_on_one_platform_have_independent_health(
        clean_env, con, monkeypatch, tmp_path):
    """The exact case the old provider panel could not express: one Bluesky
    account suspended, another live — one dot per platform is a lie in both
    directions."""
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "bluesky", "handle": "a.bsky.social",
         "opened_at": _days_ago(30), "status": "suspended"},
        {"persona": "vera", "platform": "bluesky", "handle": "b.bsky.social",
         "opened_at": _days_ago(30), "status": "active"},
    ])
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")
    cards = health.account_cards(con)
    by_handle = {c["handle"]: c for c in cards}
    assert by_handle["a.bsky.social"]["gate"]["open"] is False
    assert by_handle["a.bsky.social"]["gate"]["kind"] == "status"
    assert by_handle["b.bsky.social"]["gate"]["open"] is True
    # same platform, same machine key — different verdicts, per account
    assert by_handle["a.bsky.social"]["credentials_ok"] is True


def test_registry_status_outranks_valid_credentials(
        clean_env, con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "telegram", "handle": "ch",
         "opened_at": _days_ago(30), "status": "suspended"}])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    card = health.account_cards(con)[0]
    assert card["credentials_ok"] is True
    assert card["gate"]["kind"] == "status" and not card["gate"]["open"]


# ── gates: the local view of the guard's precedence ─────────────

def test_warmup_gate_says_when_it_ends(clean_env, con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(0.5), "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    gate = health.account_cards(con)[0]["gate"]
    assert gate["kind"] == "warmup" and not gate["open"]
    ends = datetime.fromisoformat(gate["until"])
    expected = datetime.now(UTC) - timedelta(days=0.5) + timedelta(days=2)
    assert abs((ends - expected).total_seconds()) < 120


def test_cadence_cap_closes_the_gate_until_midnight(
        clean_env, con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(30), "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    con.execute("INSERT INTO posts (platform, status, posted_at, text) "
                "VALUES ('instagram','published',datetime('now'),'x')")
    con.commit()
    gate = health.account_cards(con)[0]["gate"]  # instagram cap: 1/day
    assert gate["kind"] == "cadence" and gate["posts_today"] == 1


def test_min_gap_closes_the_gate_between_posts(clean_env, con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "telegram", "handle": "ch",
         "opened_at": _days_ago(30), "status": "active"}])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    con.execute("INSERT INTO posts (platform, status, posted_at, text) "
                "VALUES ('telegram','published',?,'x')", (stamp,))
    con.commit()
    gate = health.account_cards(con)[0]["gate"]  # telegram min gap: 2h
    assert gate["kind"] == "gap"
    ready = datetime.fromisoformat(gate["until"])
    assert abs((ready - (datetime.now(UTC) + timedelta(hours=1))).total_seconds()) < 120


# ── the inbox: human-actionable only ────────────────────────────

def test_suspension_is_critical_and_carries_the_appeal_deadline(
        clean_env, con, monkeypatch, tmp_path):
    _empty_pools(monkeypatch, tmp_path)
    suspended = datetime.now(UTC) - timedelta(days=3)
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "bluesky", "handle": "a.bsky.social",
         "opened_at": _days_ago(30), "status": "suspended",
         "suspended_at": suspended.isoformat()}])
    items = health.attention(con)
    hit = [i for i in items if i["severity"] == "critical"]
    assert hit and "suspended" in hit[0]["title"]
    due = datetime.fromisoformat(hit[0]["due"])
    assert abs((due - (suspended + timedelta(days=14))).total_seconds()) < 60


def test_warmup_and_pacing_never_reach_the_inbox(
        clean_env, con, monkeypatch, tmp_path):
    """Warm-up is the system working as designed — an inbox that lists it
    trains the operator to ignore the inbox."""
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "home-interiors", age_hours=2)
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(0.5), "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    assert health.attention(con) == []


def test_missing_credentials_on_an_active_account_is_actionable(
        clean_env, con, monkeypatch, tmp_path):
    _empty_pools(monkeypatch, tmp_path)
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(30), "status": "active"}])
    items = health.attention(con)
    hit = [i for i in items if "credentials" in i["title"]]
    assert hit and hit[0]["severity"] == "action"


def test_one_stale_pool_among_fresh_ones_names_the_category(
        clean_env, con, monkeypatch, tmp_path):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "food-drink", age_hours=60)
    _write_pool(d, "home-interiors", age_hours=2)
    _registry(monkeypatch, tmp_path, [])
    items = health.attention(con)
    hit = [i for i in items if "food-drink" in i["title"]]
    assert hit and hit[0]["severity"] == "action" and hit[0]["screen"] == "#/signals"
    assert "specifically" in hit[0]["detail"]


def test_every_pool_stale_is_one_incident_not_seven(
        clean_env, con, monkeypatch, tmp_path):
    """Seven rows for one dead routine buries the item that matters — a full
    outage collapses to a single inbox row naming the shared cause."""
    d = _empty_pools(monkeypatch, tmp_path)
    for cat in ("a", "b", "c", "d", "e", "f", "g"):
        _write_pool(d, cat, age_hours=60)
    _registry(monkeypatch, tmp_path, [])
    items = [i for i in health.attention(con) if "pool" in i["title"] or
             "harvest" in i["title"]]
    assert len(items) == 1
    assert "all 7 pools" in items[0]["title"]


def test_a_gapping_metrics_ledger_is_a_watch_item(
        clean_env, con, monkeypatch, tmp_path):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "food-drink", age_hours=2)
    _registry(monkeypatch, tmp_path, [])
    led = tmp_path / "ledger" / "telegram--ch"
    led.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    (led / "history.jsonl").write_text(json.dumps({"ts": old}) + "\n")
    items = health.attention(con)
    hit = [i for i in items if "ledger" in i["title"]]
    assert hit and hit[0]["severity"] == "watch"


def test_failed_cycle_links_straight_to_its_page(
        clean_env, con, monkeypatch, tmp_path):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "food-drink", age_hours=2)
    _registry(monkeypatch, tmp_path, [])
    cid = store.start_cycle(con, 0)
    store.finish_cycle(con, cid, "failed", "renderer exploded")
    items = health.attention(con)
    hit = [i for i in items if "cycle" in i["title"]]
    assert hit and hit[0]["screen"] == f"#/cycle/{cid}"
    assert "renderer exploded" in hit[0]["detail"]


def test_inbox_orders_by_severity_then_deadline(clean_env, con, monkeypatch, tmp_path):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "food-drink", age_hours=60)      # action
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "bluesky", "handle": "a",
         "opened_at": _days_ago(30), "status": "suspended",
         "suspended_at": _days_ago(3)}])            # critical
    items = health.attention(con)
    assert [i["severity"] for i in items][:2] == ["critical", "action"]


# ── the instagram token clock ───────────────────────────────────

def _token_file(tmp_path, monkeypatch, days_left):
    f = tmp_path / "tok.json"
    f.write_text(json.dumps({
        "token": "stored",
        "expires_at": (datetime.now(UTC) + timedelta(days=days_left)).isoformat()}))
    monkeypatch.setattr(ig, "TOKEN_FILE", f)


@pytest.mark.parametrize("days_left,severity", [
    (-1, "critical"), (2, "action"), (12, "watch")])
def test_token_expiry_escalates_with_the_clock(
        clean_env, con, monkeypatch, tmp_path, days_left, severity):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "home-interiors", age_hours=2)
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(30), "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "boot")
    _token_file(tmp_path, monkeypatch, days_left)
    hits = [i for i in health.attention(con) if "token" in i["title"].lower()]
    assert hits and hits[0]["severity"] == severity


def test_a_healthy_token_stays_out_of_the_inbox(
        clean_env, con, monkeypatch, tmp_path):
    d = _empty_pools(monkeypatch, tmp_path)
    _write_pool(d, "home-interiors", age_hours=2)
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram", "handle": "j",
         "opened_at": _days_ago(30), "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "boot")
    _token_file(tmp_path, monkeypatch, days_left=45)
    assert [i for i in health.attention(con) if "token" in i["title"].lower()] == []


# ── machine keys: inventory, not health ─────────────────────────

def test_instagram_key_is_healthy_without_a_media_host(
        clean_env, monkeypatch, tmp_path):
    """Regression for the stale red card: provider-URL passthrough removed the
    media-host requirement for generated stills, so its absence is a scoped
    note about video — never a red state."""
    _registry(monkeypatch, tmp_path, [
        {"persona": "june", "platform": "instagram",
         "handle": "athomewithjune", "status": "active"}])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "boot")
    _token_file(tmp_path, monkeypatch, days_left=59)
    ok, note = health.platform_credentials("instagram")
    assert ok is True
    assert "59d" in note and "video" in note
    row = next(r for r in health.machine_keys() if r["platform"] == "instagram")
    assert row["ok"] is True and row["serves"] == ["athomewithjune"]


def test_an_expired_token_is_a_dead_key(clean_env, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [])
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "boot")
    _token_file(tmp_path, monkeypatch, days_left=-2)
    ok, note = health.platform_credentials("instagram")
    assert ok is False and "EXPIRED" in note


def test_keys_name_the_accounts_they_serve(clean_env, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "telegram", "handle": "marabrews",
         "status": "active"},
        {"persona": "june", "platform": "instagram", "handle": "j",
         "status": "active"}])
    rows = {r["platform"]: r for r in health.machine_keys()}
    assert rows["telegram"]["serves"] == ["marabrews"]
    assert rows["bluesky"]["serves"] == []
