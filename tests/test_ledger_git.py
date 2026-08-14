"""Decisions travel back — the ledger is only a ledger if both machines see it.

The cloud drafts and the operator releases; if the resolution stays on the
operator's laptop the next cycle re-offers a draft that was already answered
and the operator's next pull starts conflicting. So resolving commits and
pushes — best effort, because a decision already taken must never be lost to
a network failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import ledger_git  # noqa: E402


def _git(repo, *args):
    return subprocess.run(("git", *args), cwd=repo, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def repo(monkeypatch, tmp_path):
    """A real repository with a real 'origin' — the push path is the point."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    _git(work, "remote", "add", "origin", str(origin))
    (work / "README").write_text("x", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "main")
    (work / "data" / "drafts" / "resolved").mkdir(parents=True)
    monkeypatch.setattr(ledger_git, "ROOT", work)
    monkeypatch.delenv("STUDIO_LEDGER_AUTOPUSH", raising=False)
    return work


def _resolve(repo, name="d1"):
    (repo / "data" / "drafts" / "resolved" / f"{name}.json").write_text(
        '{"status": "rejected"}', encoding="utf-8")


def test_a_decision_is_committed_and_pushed(repo):
    _resolve(repo)
    out = ledger_git.publish_decision("d1", "rejected")
    assert "pushed" in out
    log = subprocess.run(("git", "log", "--oneline", "-1", "origin/main"),
                         cwd=repo, capture_output=True, text=True)
    assert "drafts: rejected d1" in log.stdout


def test_nothing_to_carry_is_silent(repo):
    assert ledger_git.publish_decision("d1", "rejected") == ""


def test_only_the_ledger_is_committed(repo):
    """The operator's own work in the tree stays theirs."""
    _resolve(repo)
    (repo / "notes.txt").write_text("my private scratch", encoding="utf-8")
    ledger_git.publish_decision("d1", "rejected")
    status = subprocess.run(("git", "status", "--porcelain"), cwd=repo,
                            capture_output=True, text=True).stdout
    assert "notes.txt" in status          # still uncommitted, untouched


def test_a_failed_push_keeps_the_commit_and_says_so(repo):
    _resolve(repo)
    _git(repo, "remote", "set-url", "origin", str(repo / "nonexistent.git"))
    out = ledger_git.publish_decision("d1", "rejected")
    assert "committed locally" in out and "git push" in out
    log = subprocess.run(("git", "log", "--oneline", "-1"), cwd=repo,
                         capture_output=True, text=True).stdout
    assert "drafts: rejected d1" in log   # the decision is not lost


def test_autopush_can_be_turned_off(repo, monkeypatch):
    monkeypatch.setenv("STUDIO_LEDGER_AUTOPUSH", "0")
    _resolve(repo)
    assert ledger_git.publish_decision("d1", "rejected") == ""
    status = subprocess.run(("git", "status", "--porcelain"), cwd=repo,
                            capture_output=True, text=True).stdout
    assert "data/" in status              # left for the operator to handle
