"""One press means one post, and the operator can see it happening.

Both halves come from the same evening. The operator approved a six-slide
carousel, watched a still screen for a minute, concluded nothing had
happened and pressed again — and the console's own log shows two publishes
interleaving: slide 1, slide 2, slide 1, slide 3, slide 2, slide 3. Only
luck stopped two identical posts appearing on the account.

The claim stops the duplicate. The progress line stops the silence, which
is what caused the second press in the first place — and the second is the
real fix, because a person who can see the work happening does not press
again.
"""

import json
import time

from studio import draftpool, progress


def _draft(tmp_path, monkeypatch, draft_id="d1") -> str:
    monkeypatch.setattr(draftpool, "PENDING_DIR", tmp_path)
    (tmp_path / f"{draft_id}.json").write_text(json.dumps(
        {"id": draft_id, "persona": "june", "platform": "instagram",
         "status": "pending", "text": "hi"}), encoding="utf-8")
    return draft_id


# ── one press, one post ─────────────────────────────────────────

def test_a_second_press_is_refused_while_the_first_is_in_flight(tmp_path, monkeypatch):
    did = _draft(tmp_path, monkeypatch)
    assert draftpool.begin_release(did) == ""
    refusal = draftpool.begin_release(did)
    assert "already being published" in refusal
    assert "twice" in refusal          # says WHY, not just no


def test_the_claim_is_dropped_so_a_real_retry_still_works(tmp_path, monkeypatch):
    """A failed publish must leave the draft releasable — the operator fixes
    the token and presses again."""
    did = _draft(tmp_path, monkeypatch)
    draftpool.begin_release(did)
    draftpool.end_release(did)
    assert draftpool.begin_release(did) == ""


def test_a_claim_from_a_dead_process_expires(tmp_path, monkeypatch):
    """A killed console or a slept machine must not strand a draft forever.
    The window is longer than a reel's transcode so it never fires early."""
    did = _draft(tmp_path, monkeypatch)
    draftpool.begin_release(did)
    assert draftpool.RELEASING_STALE_SECONDS > 300      # longer than POLL_MAX
    d = draftpool.get(did)
    d["releasing_at"] = "2020-01-01T00:00:00+00:00"
    (tmp_path / f"{did}.json").write_text(json.dumps(d), encoding="utf-8")
    assert draftpool.begin_release(did) == ""


def test_a_draft_that_vanished_is_not_claimable(tmp_path, monkeypatch):
    monkeypatch.setattr(draftpool, "PENDING_DIR", tmp_path)
    assert "not pending" in draftpool.begin_release("gone")


# ── and the operator can watch it ───────────────────────────────

def test_progress_is_reported_and_read_back():
    progress.clear("x")
    progress.bind("x")
    progress.note("uploading slide 2 of 6", 2, 8)
    state = progress.get("x")
    assert state["text"] == "uploading slide 2 of 6"
    assert (state["step"], state["total"]) == (2, 8)
    progress.clear("x")


def test_two_releases_on_one_console_never_cross(monkeypatch):
    """Thread-local, because the interleaved log the operator saw was two
    publishes running at once — one line each, not one line fought over."""
    import threading

    progress.clear("a")
    progress.clear("b")

    def run(key, text):
        progress.bind(key)
        progress.note(text)

    threads = [threading.Thread(target=run, args=("a", "first")),
               threading.Thread(target=run, args=("b", "second"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert progress.get("a")["text"] == "first"
    assert progress.get("b")["text"] == "second"
    progress.clear("a")
    progress.clear("b")


def test_an_unwatched_report_is_harmless():
    """factory and adapter code calls note() whether or not a console is
    listening; without a bound key it must simply do nothing."""
    progress.bind("")
    progress.note("into the void")
    assert progress.get("") is None


def test_a_finished_line_does_not_linger_forever(monkeypatch):
    progress.clear("z")
    progress.bind("z")
    progress.note("published")
    monkeypatch.setattr(time, "time", lambda: time.__dict__ and 1e12)
    assert progress.get("z") is None


def test_the_console_serves_the_progress_and_the_button_polls_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "dashboard" / "serve.py").read_text(encoding="utf-8")
    assert '/api/release_progress' in src
    assert 'watchRelease(id,btn)' in src
    # and only a release is watched — rejecting is instant
    assert 'const stop=(action==="approve")?watchRelease(id,btn):null;' in src
