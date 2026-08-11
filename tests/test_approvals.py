"""Approval-queue tests — the operator's hand between a draft and the world.

publish_mode: approve means a cycle finishes everything and presses nothing;
the console releases the post. These pin the mode lookup, the draft store,
the re-checked guard at release time, and that stock-first personas can
never quietly reach paid generation.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import approvals, deliver, guard, metrics, store  # noqa: E402


@pytest.fixture
def con(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    return store.connect()


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


def test_publish_mode_defaults_to_auto_and_reads_the_registry(
        monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [
        _telegram_row(publish_mode="approve"),
        {"persona": "june", "platform": "instagram", "handle": "j",
         "status": "active"}])
    assert guard.publish_mode("telegram", "mara") == "approve"
    assert guard.publish_mode("instagram", "june") == "auto"
    assert guard.publish_mode("bluesky", "nobody") == "auto"


def test_draft_roundtrip(con):
    did = store.save_draft(con, 1, "june", "instagram", "/a/b.jpg", "image",
                           "alt", "caption text", tags=["t"],
                           provenance={"model": "m", "style": "june-v1"})
    ds = store.pending_drafts(con)
    assert len(ds) == 1 and ds[0]["id"] == did
    assert ds[0]["provenance"]["style"] == "june-v1" and ds[0]["tags"] == ["t"]
    store.resolve_draft(con, did, "rejected", "not her world")
    assert store.pending_drafts(con) == []
    assert store.get_draft(con, did)["status"] == "rejected"


def test_approve_republishes_through_the_shared_dispatch(
        con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row()])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    sent = {}

    def fake_publish(platform, rendition, fallback, media, kind, alt,
                     provenance, persona_id, hero=False):
        sent.update(platform=platform, text=rendition["text"], media=media)
        return {"uri": "tg:1", "url": "https://t.me/ch/1"}

    monkeypatch.setattr(deliver, "publish", fake_publish)
    did = store.save_draft(con, 1, "mara", "telegram", "/a.jpg", "image",
                           "alt", "quiet caption")
    out = approvals.approve(con, did)
    assert out["ok"] is True and out["url"].startswith("https://t.me")
    assert sent["platform"] == "telegram" and sent["text"] == "quiet caption"
    assert store.get_draft(con, did)["status"] == "approved"
    row = con.execute("SELECT status, url FROM posts").fetchone()
    assert row["status"] == "published" and row["url"] == "https://t.me/ch/1"


def test_a_closed_gate_keeps_the_draft_pending(con, monkeypatch, tmp_path):
    """Approval can come hours later — the guard's verdict belongs to the
    moment of release, and a refusal must not consume the draft."""
    _registry(monkeypatch, tmp_path, [_telegram_row(status="suspended")])
    did = store.save_draft(con, 1, "mara", "telegram", "/a.jpg", "image",
                           "alt", "caption")
    out = approvals.approve(con, did)
    assert out["ok"] is False and "suspended" in out["message"]
    d = store.get_draft(con, did)
    assert d["status"] == "pending" and "held:" in d["note"]


def test_a_failed_publish_marks_the_draft_failed(con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row()])
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@ch")
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))

    def boom(*a, **k):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(deliver, "publish", boom)
    did = store.save_draft(con, 1, "mara", "telegram", "/a.jpg", "image",
                           "alt", "caption")
    out = approvals.approve(con, did)
    assert out["ok"] is False and "telegram is down" in out["message"]
    assert store.get_draft(con, did)["status"] == "failed"


def test_resolved_drafts_cannot_be_released_twice(con, monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, [_telegram_row()])
    did = store.save_draft(con, 1, "mara", "telegram", "/a.jpg", "image",
                           "alt", "caption")
    assert approvals.reject(con, did)["ok"] is True
    assert approvals.reject(con, did)["ok"] is False
    assert approvals.approve(con, did)["ok"] is False


def test_missing_source_url_needs_no_network():
    out = approvals._fresh_source_url({"model": "m"}, "/a.jpg")
    assert out == {"model": "m"}


def test_dead_provider_url_is_dropped_when_reupload_fails(monkeypatch):
    import fal_client
    import httpx

    class Dead:
        status_code = 404

    monkeypatch.setattr(httpx, "head", lambda *a, **k: Dead())

    def no_upload(path):
        raise RuntimeError("no storage")

    monkeypatch.setattr(fal_client, "upload_file", no_upload)
    out = approvals._fresh_source_url(
        {"source_url": "https://v3.fal.media/x.png"}, "/a.jpg")
    assert out["source_url"] == ""  # media_host will now decide, loudly


def test_stock_first_never_reaches_paid_generation(monkeypatch, tmp_path):
    """Mara's Telegram runs on licensed stock — a broken stock key must fall
    to the free local renderer, never to a paid render."""
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
