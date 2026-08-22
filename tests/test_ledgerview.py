"""The Pipeline screen must count from the git ledger, not the local db.

Production cycles run in the cloud and commit reports and drafts to git;
the local SQLite store only knows cycles started on the same machine. The
operator, three days before a CEO demo: "o kadar işlem yaptık, pipeline
hep boş gözüküyor" — ten cloud cycles were showing as six zeros.
"""

import json

from studio import draftpool, ledgerview


def _report(dirpath, stem, body):
    (dirpath / f"{stem}.md").write_text(body, encoding="utf-8")


def _draft(dirpath, name, **fields):
    (dirpath / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")


def test_runs_come_from_report_files_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(ledgerview, "REPORTS_DIR", tmp_path)
    _report(tmp_path, "cycle-2026-08-21-1414",
            "# autoStudio content cycle\n\nx. **Mixed outcome: June drafted "
            "(2); Mara FAILED again on TTS.**\n")
    _report(tmp_path, "cycle-2026-08-22-1411",
            "# autoStudio content cycle\n\nx. **June drafted (2); all gates "
            "passed cleanly.**\n")
    runs = ledgerview.cycle_runs()
    assert [r["when"] for r in runs] == ["2026-08-22 14:11", "2026-08-21 14:14"]
    assert runs[0]["ok"] is True
    assert runs[1]["ok"] is False            # its own verdict says FAILED
    assert runs[1]["outcome"].startswith("Mixed outcome")


def test_short_bold_fragments_are_not_mistaken_for_the_verdict(monkeypatch, tmp_path):
    """Reports bold field labels too — **June — Instagram: DRAFTED (2)**
    comes later, but things like **now urgent** come first and are not a
    verdict."""
    monkeypatch.setattr(ledgerview, "REPORTS_DIR", tmp_path)
    _report(tmp_path, "cycle-2026-08-20-1415",
            "# report\n\n**now urgent**\n\nThen **Mixed outcome: June "
            "drafted (2); Mara FAILED.** More text.\n")
    (run,) = ledgerview.cycle_runs()
    assert run["outcome"].startswith("Mixed outcome")


def test_totals_count_the_whole_ledger(monkeypatch, tmp_path):
    reports = tmp_path / "reports"; reports.mkdir()
    pending = tmp_path / "pending"; pending.mkdir()
    resolved = tmp_path / "resolved"; resolved.mkdir()
    monkeypatch.setattr(ledgerview, "REPORTS_DIR", reports)
    monkeypatch.setattr(draftpool, "PENDING_DIR", pending)
    monkeypatch.setattr(draftpool, "RESOLVED_DIR", resolved)
    _report(reports, "cycle-2026-08-20-1415", "**ok**")
    _draft(pending, "a", status="pending", persona="june",
           media_files=["a.jpg", "a-2.jpg"])
    _draft(resolved, "b", status="approved", persona="june", media_file="b.mp4")
    _draft(resolved, "c", status="posted_by_hand", persona="mara",
           media_file="c.mp4")
    _draft(resolved, "d", status="rejected", persona="june", media_file="d.jpg")
    t = ledgerview.totals()
    assert t == {"cycles": 1, "drafts": 4, "published": 2, "rejected": 1,
                 "waiting": 1, "media": 5, "personas": 2}


def test_reports_are_served_by_basename_only(monkeypatch, tmp_path):
    """The /report endpoint hands this function raw query input."""
    monkeypatch.setattr(ledgerview, "REPORTS_DIR", tmp_path)
    _report(tmp_path, "cycle-2026-08-22-1411", "hello")
    assert ledgerview.report_path("cycle-2026-08-22-1411.md") is not None
    assert ledgerview.report_path("../.env") is None
    assert ledgerview.report_path("cycle-../../.env.md") is None
    assert ledgerview.report_path("notes.md") is None
    assert ledgerview.report_path("cycle-missing.md") is None


def _pool_dirs(monkeypatch, tmp_path):
    pending = tmp_path / "pending"; pending.mkdir()
    resolved = tmp_path / "resolved"; resolved.mkdir()
    media = tmp_path / "media"; media.mkdir()
    monkeypatch.setattr(draftpool, "PENDING_DIR", pending)
    monkeypatch.setattr(draftpool, "RESOLVED_DIR", resolved)
    monkeypatch.setattr(draftpool, "MEDIA_DIR", media)
    return pending, resolved, media


def test_published_lists_only_successes_with_their_links(monkeypatch, tmp_path):
    pending, resolved, media = _pool_dirs(monkeypatch, tmp_path)
    (media / "a.mp4").write_bytes(b"v")
    _draft(resolved, "a", status="approved", persona="june",
           platform="instagram", media_kind="video", media_file="a.mp4",
           text="hello", note="https://instagram.com/p/X/",
           resolved_at="2026-08-21T10:00:00+00:00")
    _draft(resolved, "b", status="rejected", persona="june",
           platform="instagram", text="no", resolved_at="2026-08-22T10:00:00+00:00")
    _draft(pending, "c", status="pending", persona="june", text="waiting")
    (rows := ledgerview.published())
    assert [r["url"] for r in rows] == ["https://instagram.com/p/X/"]
    assert rows[0]["media"] == [str(media / "a.mp4")]


def test_a_reject_note_is_never_mistaken_for_a_link(monkeypatch, tmp_path):
    _, resolved, _ = _pool_dirs(monkeypatch, tmp_path)
    _draft(resolved, "a", status="posted_by_hand", persona="june",
           platform="instagram", text="x",
           note="posted from the app by the operator",
           resolved_at="2026-08-21T10:00:00+00:00")
    assert ledgerview.published()[0]["url"] == ""


def test_the_gallery_walks_ledger_media_newest_first(monkeypatch, tmp_path):
    pending, resolved, media = _pool_dirs(monkeypatch, tmp_path)
    for name in ("old.jpg", "new.jpg", "new-2.mp4", "cov.jpg"):
        (media / name).write_bytes(b"x")
    _draft(resolved, "old", status="approved", persona="june",
           media_file="old.jpg", created_at="2026-08-20T09:00:00+00:00")
    _draft(pending, "new", status="pending", persona="june",
           media_files=["new.jpg", "new-2.mp4"], cover_file="cov.jpg",
           created_at="2026-08-22T09:00:00+00:00")
    g = ledgerview.media_gallery()
    assert [x["path"].rsplit("/", 1)[-1] for x in g] == \
        ["new.jpg", "new-2.mp4", "old.jpg"]
    assert g[1]["kind"] == "video" and g[1]["poster"].endswith("cov.jpg")
    assert g[2]["status"] == "approved"


def test_published_today_counts_from_the_ledger(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    _, resolved, _ = _pool_dirs(monkeypatch, tmp_path)
    today = datetime.now(UTC).date().isoformat()
    _draft(resolved, "a", status="approved", platform="instagram", text="x",
           resolved_at=f"{today}T08:00:00+00:00")
    _draft(resolved, "b", status="posted_by_hand", platform="instagram",
           text="y", resolved_at="2026-08-01T08:00:00+00:00")
    _draft(resolved, "c", status="approved", platform="telegram", text="z",
           resolved_at=f"{today}T09:00:00+00:00")
    count, last = ledgerview.published_today("instagram")
    assert count == 1
    assert last == f"{today}T08:00:00+00:00"


def test_the_dedupe_gate_sees_ledger_successes(monkeypatch, tmp_path):
    """The posts table is empty on the drafting machine (fresh clone every
    day), so the gate compared against nothing in production. It must catch
    a caption that the ledger says already published."""
    import sqlite3

    from studio import guard
    _, resolved, _ = _pool_dirs(monkeypatch, tmp_path)
    _draft(resolved, "a", status="approved", platform="instagram",
           text="The Amber glass doesn't change!",
           resolved_at="2026-08-21T10:00:00+00:00")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, text TEXT)")
    assert guard.is_duplicate_caption(con, "the amber glass doesnt change") is True
    assert guard.is_duplicate_caption(con, "a completely new idea") is False


def test_the_gate_preview_counts_ledger_successes_too(monkeypatch, tmp_path):
    """health's posts-today preview read only the machine-local table, so a
    fresh checkout showed 0 posted today while the ledger carried the
    releases."""
    import sqlite3
    from datetime import UTC, datetime

    from studio import health
    _, resolved, _ = _pool_dirs(monkeypatch, tmp_path)
    today = datetime.now(UTC).date().isoformat()
    _draft(resolved, "a", status="approved", platform="instagram", text="x",
           resolved_at=f"{today}T08:00:00+00:00")
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, text TEXT, "
                "platform TEXT, status TEXT, posted_at TEXT)")
    count, last = health._local_posts(con, "instagram")
    assert count == 1
    assert last == f"{today}T08:00:00+00:00"
