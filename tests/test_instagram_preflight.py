"""Instagram key preflight — tell the operator what is wrong BEFORE a release.

The operator pasted a placeholder user id and got 'HTTP 400 Failed to
decrypt' back from Meta — an error that names nothing an operator can act
on. preflight() asks the token who it is (with Instagram Login, /me resolves
the account from the token alone) and reports mismatches in the operator's
own terms.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import media_host, publisher_instagram as ig  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for var in ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN",
                "INSTAGRAM_HANDLE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ig, "TOKEN_FILE", tmp_path / "tok.json")
    monkeypatch.setattr(media_host, "configured", lambda: True)


def _token_is(monkeypatch, user_id="17841999", username="athomewithjunecaprio"):
    monkeypatch.setattr(ig, "whoami",
                        lambda: {"user_id": user_id, "username": username})


def test_empty_keys_are_named_not_guessed(monkeypatch):
    problems = ig.preflight()
    assert any("INSTAGRAM_ACCESS_TOKEN is empty" in p for p in problems)
    assert any("INSTAGRAM_USER_ID is empty" in p for p in problems)


def test_placeholder_user_id_is_caught_with_the_real_one(monkeypatch):
    """The exact trap: a copied example id with a valid token."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA-real")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")
    _token_is(monkeypatch)
    problems = ig.preflight()
    assert len(problems) == 1
    assert "17841999" in problems[0]          # names the id to paste
    assert "athomewithjunecaprio" in problems[0]


def test_token_for_the_wrong_account_is_refused(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA-real")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_HANDLE", "athomewithjunecaprio")
    _token_is(monkeypatch, username="someoneelse")
    problems = ig.preflight()
    assert any("different account" in p for p in problems)


def test_rejected_token_says_so_plainly(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA-bad")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")

    def boom():
        raise RuntimeError("the token itself was rejected: HTTP 400 "
                           "Failed to decrypt")

    monkeypatch.setattr(ig, "whoami", boom)
    problems = ig.preflight()
    assert len(problems) == 1
    assert "Failed to decrypt" in problems[0]
    assert "generate a fresh one" in problems[0]


def test_matching_keys_pass_clean(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA-real")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_HANDLE", "@AtHomeWithJuneCaprio")  # case/@ ok
    _token_is(monkeypatch)
    assert ig.preflight() == []


def test_missing_media_host_is_reported_last(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA-real")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    _token_is(monkeypatch)
    monkeypatch.setattr(media_host, "configured", lambda: False)
    problems = ig.preflight()
    assert problems and "media host" in problems[-1]
