"""Drafts the operator released themselves, and the record of who asked.

Some drafts can only go out by hand: a reel needs a trending track chosen
inside the Instagram app, and no API can add audio to a reel after it
exists. Those used to sit in the queue for good, indistinguishable from
work not yet done — "elle paylasiyoruz ve kuyrukta hala kaliyor".
"""

import json

import pytest

from studio import draftpool


@pytest.fixture
def queue(tmp_path, monkeypatch):
    for name, sub in (("DRAFTS_DIR", ""), ("PENDING_DIR", "pending"),
                      ("RESOLVED_DIR", "resolved"), ("MEDIA_DIR", "media")):
        p = tmp_path / sub if sub else tmp_path
        p.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(draftpool, name, p)
    monkeypatch.setattr(draftpool, "RELEASE_LOG", tmp_path / "release-log.jsonl")

    def add(draft_id, **extra):
        (tmp_path / "pending" / f"{draft_id}.json").write_text(json.dumps(
            {"id": draft_id, "persona": "june", "platform": "instagram",
             "text": "a caption", "media_kind": "video", "status": "pending",
             **extra}), encoding="utf-8")
    return add


def test_a_hand_released_draft_leaves_the_queue_as_a_success(queue):
    queue("d1")
    draftpool.resolve("d1", draftpool.POSTED_BY_HAND,
                      "https://instagram.com/reel/X")
    assert draftpool.get("d1") is None, "it must leave the pending list"
    done = draftpool.resolved()[0]
    assert done["status"] == draftpool.POSTED_BY_HAND
    assert done["status"] in draftpool.SUCCESS_STATUSES
    assert done["status"] != draftpool.REJECTED, (
        "a hand-released post must never read as a rejection — learning.py "
        "drops rejected drafts from attribution")
    assert done["note"] == "https://instagram.com/reel/X"


def test_a_success_clears_the_error_of_an_earlier_attempt(queue):
    """A resolved draft reading "published" while still carrying "publish
    failed" is what made the operator ask whether the post went out at all.
    The history is kept, just not as the CURRENT state."""
    queue("d2")
    draftpool.stamp_error("d2", "HTTP 400 alt_text is not supported for REEL")
    assert draftpool.get("d2")["last_error"]

    draftpool.resolve("d2", draftpool.APPROVED, "https://instagram.com/reel/Y")
    done = draftpool.resolved()[0]
    assert "last_error" not in done and "last_error_at" not in done
    assert done["earlier_errors"][0]["error"].startswith("HTTP 400")


def test_a_rejection_keeps_its_error_where_it_is(queue):
    """Only a SUCCESS makes an earlier failure stale. A draft turned down
    after a failed release still has that failure as its current state."""
    queue("d3")
    draftpool.stamp_error("d3", "media host unreachable")
    draftpool.resolve("d3", draftpool.REJECTED, "operator said no")
    assert draftpool.resolved()[0]["last_error"] == "media host unreachable"


def test_every_release_request_is_logged_with_where_it_came_from(queue):
    """The console could not answer the one question that matters after a
    surprise: who asked for this. A release failed, the operator restarted,
    and sixteen minutes later the post was live with nothing able to say
    whether that was a second click or something else."""
    draftpool.log_release("d4", "approve", "127.0.0.1 · Safari", "started")
    draftpool.log_release("d4", "approve", "same request", "failed: HTTP 400")
    draftpool.log_release("d4", "approve", "127.0.0.1 · Safari", "started")
    draftpool.log_release("d4", "approve", "same request",
                          "published https://instagram.com/reel/Z")

    rows = draftpool.release_log()
    assert len(rows) == 4
    assert rows[0]["result"].startswith("published")     # newest first
    assert [r["source"] for r in rows].count("127.0.0.1 · Safari") == 2
    assert all(r["at"] for r in rows)


def test_the_log_never_breaks_a_release(queue, monkeypatch):
    """A log that stops a publish is worse than no log."""
    monkeypatch.setattr(draftpool, "RELEASE_LOG",
                        draftpool.RELEASE_LOG / "nope" / "x.jsonl")
    draftpool.log_release("d5", "approve", "somewhere", "started")   # no raise


def test_silence_is_measured_not_assumed():
    """Every reel this pipeline makes carries an AAC track. A style-morph
    reel has the voice naming each style over music; a material-board reel
    has an equally valid track of pure silence, because that format expects
    a trending song added in the app. Both report "Audio: aac, stereo" —
    only the LEVEL tells them apart, and getting it wrong is what led to
    telling the operator a live post needed re-doing when it did not."""
    import subprocess
    from pathlib import Path

    from studio import factory
    try:
        ff = factory.ffmpeg_bin()
    except Exception:
        pytest.skip("no ffmpeg on this box")

    import tempfile
    tmp = Path(tempfile.mkdtemp())
    silent, loud = tmp / "silent.mp4", tmp / "loud.mp4"
    common = ["-f", "lavfi", "-i", "color=c=black:s=64x64:d=2"]
    subprocess.run([ff, "-y", "-v", "error", *common, "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "2", "-shortest",
                    str(silent)], check=True)
    subprocess.run([ff, "-y", "-v", "error", *common, "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100", "-t", "2",
                    "-shortest", str(loud)], check=True)

    assert factory.has_audible_sound(str(silent)) is False
    assert factory.has_audible_sound(str(loud)) is True
    assert factory.has_audible_sound(str(tmp / "missing.mp4")) is None
