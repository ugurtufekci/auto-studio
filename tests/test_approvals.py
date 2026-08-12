"""Approval-queue tests — drafts travel by git, release happens by hand.

publish_mode: approve means a cycle finishes everything and presses nothing;
the draft rides the git ledger to wherever the console runs, and releasing
it re-checks every gate — including the credential binding, which is
deliberately deferred from cycle time to release time for approve-mode
accounts (the cloud routine that drafts holds no platform keys at all).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import approvals, deliver, draftpool, guard, metrics, store  # noqa: E402


@pytest.fixture
def con(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    return store.connect()


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    base = tmp_path / "drafts"
    monkeypatch.setattr(draftpool, "DRAFTS_DIR", base)
    monkeypatch.setattr(draftpool, "PENDING_DIR", base / "pending")
    monkeypatch.setattr(draftpool, "RESOLVED_DIR", base / "resolved")
    monkeypatch.setattr(draftpool, "MEDIA_DIR", base / "media")
    return base


def _registry(monkeypatch, tmp_path, accounts):
    reg = tmp_path / "accounts.yaml"
    reg.write_text(yaml.safe_dump({"accounts": accounts}))
    monkeypatch.setattr(metrics, "REGISTRY", reg)


def _telegram_row(**over):
    row = {"persona": "mara", "platform": "telegram", "handle": "ch",
           "opened_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
           "status": "active"}
    row.update(over)
    return row


def _draft(tmp_path, **over):
    media = tmp_path / "winner.jpg"
    media.write_bytes(b"pixels")
    fields = {"brief_id": 1, "persona": "mara", "platform": "telegram",
              "media_kind": "image", "alt": "alt", "text": "quiet caption",
              "provenance": {"model": "pexels"}}
    fields.update(over)
    return draftpool.export_draft(fields, media_src=media)


# ── the ledger itself ───────────────────────────────────────────

def test_ledger_roundtrip_with_media(ledger, tmp_path):
    gid = _draft(tmp_path)
    ds = draftpool.pending()
    assert len(ds) == 1 and ds[0]["id"] == gid
    assert draftpool.media_path(ds[0]).read_bytes() == b"pixels"
    draftpool.resolve(gid, "rejected", "not her world")
    assert draftpool.pending() == []
    resolved = (ledger / "resolved" / f"{gid}.json").read_text()
    assert "rejected" in resolved and "not her world" in resolved


def test_a_corrupt_pending_file_hides_only_itself(ledger, tmp_path):
    _draft(tmp_path)
    (ledger / "pending" / "broken.json").write_text("{not json")
    assert len(draftpool.pending()) == 1


# ── the mode: cycle drafts without keys, release demands them ───

def test_binding_is_deferred_at_cycle_time_for_approve_mode(
        con, monkeypatch, tmp_path):
    """The cloud routine that drafts holds no platform keys — an approve-mode
    account must pass the cycle gate without them."""
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"):
        monkeypatch.delenv(var, raising=False)
    _registry(monkeypatch, tmp_path, [_telegram_row(publish_mode="approve")])
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    ok, why = guard.can_post(con, "telegram", persona_id="mara")
    assert ok is True, why
    # ...but the same machine may NOT release it
    ok, why = guard.can_post(con, "telegram", persona_id="mara", at_release=True)
    assert ok is False and "TELEGRAM_CHANNEL" in why


def test_approve_republishes_through_the_shared_dispatch(
        con, ledger, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row(publish_mode="approve")])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    sent = {}

    def fake_publish(platform, rendition, fallback, media, kind, alt,
                     provenance, persona_id, hero=False):
        sent.update(platform=platform, text=rendition["text"], media=media)
        return {"uri": "tg:1", "url": "https://t.me/ch/1"}

    monkeypatch.setattr(deliver, "publish", fake_publish)
    gid = _draft(tmp_path)
    out = approvals.approve(con, gid)
    assert out["ok"] is True and out["url"].startswith("https://t.me")
    assert sent["text"] == "quiet caption" and sent["media"].endswith(".jpg")
    assert draftpool.pending() == []
    row = con.execute("SELECT status, url FROM posts").fetchone()
    assert row["status"] == "published" and row["url"] == "https://t.me/ch/1"


def test_a_closed_gate_keeps_the_draft_pending(con, ledger, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row(status="suspended")])
    gid = _draft(tmp_path)
    out = approvals.approve(con, gid)
    assert out["ok"] is False and "suspended" in out["message"]
    assert draftpool.get(gid)["status"] == "pending"  # not consumed


def test_a_failed_publish_marks_the_draft_failed(con, ledger, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row(publish_mode="approve")])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))

    def boom(*a, **k):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(deliver, "publish", boom)
    gid = _draft(tmp_path)
    out = approvals.approve(con, gid)
    assert out["ok"] is False and "telegram is down" in out["message"]
    assert draftpool.get(gid) is None
    assert (ledger / "resolved" / f"{gid}.json").exists()


def test_resolved_drafts_cannot_be_released_twice(con, ledger, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row()])
    gid = _draft(tmp_path)
    assert approvals.reject(con, gid)["ok"] is True
    assert approvals.reject(con, gid)["ok"] is False
    assert approvals.approve(con, gid)["ok"] is False


# ── media materialisation across machines ───────────────────────

def test_missing_ledger_media_and_dead_url_fail_with_the_fix_named(
        con, ledger, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row(publish_mode="approve")])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    gid = draftpool.export_draft(
        {"persona": "mara", "platform": "telegram", "media_kind": "image",
         "text": "t", "provenance": {}})  # no media, no source_url
    out = approvals.approve(con, gid)
    assert out["ok"] is False and "git pull" in out["message"]
    assert draftpool.get(gid)["status"] == "pending"  # fixable — not consumed


def test_stock_first_never_reaches_paid_generation(monkeypatch, tmp_path):
    from studio import factory, source_pexels

    def paid(*a, **k):
        raise AssertionError("paid generation must never be called for stock-first")

    monkeypatch.setattr(factory, "_run_with_fallback", paid)
    monkeypatch.setattr(source_pexels, "configured", lambda: True)
    monkeypatch.setattr(
        source_pexels, "search_photos",
        lambda prompt, run_dir, n: [{"path": str(tmp_path / "s.jpg"),
                                     "prompt": prompt, "model": "pexels",
                                     "credit": {"author": "x"}}])
    out = factory.generate_images(["a cafe corner"], tmp_path, per_prompt=2,
                                  prefer="stock")
    assert out and out[0]["model"] == "pexels"
