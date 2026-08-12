"""Operator editing on held drafts — the pen between draft and release.

A small wording fix must not force a reject: the operator edits the caption
of a PENDING record in place, the edit is stamped for the audit trail, and
release still composes disclosure + limit on top of the edited text (so the
disclosure can never be edited away). Everything is written as explicit
UTF-8 — the ledger crosses machines, and a Turkish-locale Windows must read
back the same bytes a Linux cycle wrote.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import draftpool  # noqa: E402


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    base = tmp_path / "drafts"
    monkeypatch.setattr(draftpool, "DRAFTS_DIR", base)
    monkeypatch.setattr(draftpool, "PENDING_DIR", base / "pending")
    monkeypatch.setattr(draftpool, "RESOLVED_DIR", base / "resolved")
    monkeypatch.setattr(draftpool, "MEDIA_DIR", base / "media")
    return base


def _seed(text="The rug corners the chair.\n\n#quiet"):
    return draftpool.export_draft({
        "persona": "june", "platform": "instagram",
        "media_kind": "image", "text": text, "alt": "a room",
        "provenance": {"model": "fal-ai/test", "style": "june-v1"},
    })


def test_edit_replaces_text_and_stamps(ledger):
    did = _seed()
    out = draftpool.edit_text(did, "Three pillows, all different.\n\n#quiet")
    assert out["text"].startswith("Three pillows")
    assert out["edited_at"]
    d = draftpool.get(did)
    assert d["text"] == out["text"]
    assert d["status"] == "pending"          # still held, not resolved
    assert d["persona"] == "june"            # everything else untouched
    assert d["provenance"]["style"] == "june-v1"


def test_edit_roundtrips_utf8_bytes(ledger):
    """The regression that motivated this: an emoji must survive the ledger
    byte-for-byte regardless of the machine's locale encoding."""
    did = _seed()
    draftpool.edit_text(did, "café corner 🤖 stays a café corner")
    raw = (draftpool.PENDING_DIR / f"{did}.json").read_bytes()
    d = json.loads(raw.decode("utf-8"))      # strict utf-8, no locale
    assert "🤖" in d["text"] and "café" in d["text"]


def test_edit_strips_and_rejects_empty(ledger):
    did = _seed()
    with pytest.raises(ValueError):
        draftpool.edit_text(did, "   \n  ")
    assert draftpool.get(did)["text"].startswith("The rug")  # unchanged


def test_edit_unknown_draft_raises(ledger):
    with pytest.raises(FileNotFoundError):
        draftpool.edit_text("nope-123", "hello")


def test_edited_text_is_what_release_would_compose(ledger):
    """approve() composes from the record's text at release time, so an
    edit must be exactly what the composer sees afterwards."""
    from studio.publisher import compose_plain
    did = _seed()
    draftpool.edit_text(did, "New caption after the operator's pen. #quiet")
    d = draftpool.get(did)
    final = compose_plain(d["text"], 2200, d.get("provenance"), d["persona"])
    assert final.startswith("New caption after the operator's pen.")
    assert "🤖 AI-generated" in final         # disclosure survives every edit
