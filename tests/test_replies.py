"""Reply drafts: written by the studio, sent by a human.

The line this module must never cross is posting. Everything else it does —
reading comments, drafting, refusing to draft — is in service of the
operator answering faster, not of the machine answering for them.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import replies  # noqa: E402

THREAD = {"comment_id": "c1", "comment": "what green is that?",
          "author": "someone", "at": "2026-08-16T10:00:00+0000",
          "post_url": "https://x/p/1", "caption": "five material worlds"}


def _tray(monkeypatch, tmp_path):
    monkeypatch.setattr(replies, "PENDING_DIR", tmp_path / "pending")
    monkeypatch.setattr(replies, "DONE_DIR", tmp_path / "done")


def test_a_drafted_reply_waits_in_the_tray_and_is_never_posted(monkeypatch, tmp_path):
    _tray(monkeypatch, tmp_path)
    monkeypatch.setattr(replies, "fetch_threads", lambda *a, **k: [THREAD])
    monkeypatch.setattr(replies, "draft_reply",
                        lambda *a, **k: {"reply": "sage, about #9CAF88 — it goes "
                                                  "grey in north light.",
                                         "why": "answers the actual question"})
    written = replies.refresh("june")
    assert len(written) == 1
    row = replies.pending()[0]
    assert row["status"] == "pending" and row["comment_id"] == "c1"
    assert row["voice_problems"] == []      # linted like any other text
    # nothing in this module can post; the tray is the whole output
    assert not hasattr(replies, "post_reply") and not hasattr(replies, "send")


def test_silence_is_a_legitimate_draft(monkeypatch, tmp_path):
    """A hostile or spam comment gets no reply — and no card in the tray
    asking the operator to decide about it again tomorrow."""
    _tray(monkeypatch, tmp_path)
    monkeypatch.setattr(replies, "fetch_threads", lambda *a, **k: [THREAD])
    monkeypatch.setattr(replies, "draft_reply",
                        lambda *a, **k: {"reply": "", "why": "spam"})
    assert replies.refresh("june") == []
    assert replies.pending() == []


def test_a_comment_is_drafted_for_once(monkeypatch, tmp_path):
    _tray(monkeypatch, tmp_path)
    monkeypatch.setattr(replies, "fetch_threads", lambda *a, **k: [THREAD])
    monkeypatch.setattr(replies, "draft_reply",
                        lambda *a, **k: {"reply": "sage green.", "why": "short"})
    assert len(replies.refresh("june")) == 1
    assert replies.refresh("june") == [], "the same comment must not queue twice"

    # and once dismissed it stays dismissed
    rid = replies.pending()[0]["id"]
    assert replies.resolve(rid, "skipped") is True
    assert replies.pending() == []
    assert replies.refresh("june") == []


def test_bait_in_a_reply_is_flagged_for_the_operator(monkeypatch, tmp_path):
    _tray(monkeypatch, tmp_path)
    monkeypatch.setattr(replies, "fetch_threads", lambda *a, **k: [THREAD])
    monkeypatch.setattr(replies, "draft_reply",
                        lambda *a, **k: {"reply": "follow for more like this!",
                                         "why": "bad"})
    row = replies.refresh("june")[0]
    assert row["voice_problems"], "the voice contract covers replies too"
