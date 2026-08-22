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
