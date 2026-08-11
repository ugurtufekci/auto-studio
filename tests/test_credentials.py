"""Credential-binding tests — the cross-posting firewall.

With two accounts on one platform (June's Bluesky beside Mara's), a June
cycle that finds Mara's BLUESKY_HANDLE in the environment would publish
June's content into Mara's account — the worst identity incident short of a
ban. These pin the two mechanisms that prevent it: the per-persona env
overlay, and the rule that identity-bearing credentials must MATCH the
registry handle of the account being published.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import credentials, guard, metrics, store  # noqa: E402


@pytest.fixture(autouse=True)
def _no_leaked_env(monkeypatch):
    for var in ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL", "PLATFORMS"):
        monkeypatch.delenv(var, raising=False)
    for var in list(os.environ):
        if "__JUNE" in var or "__MARA" in var:
            monkeypatch.delenv(var, raising=False)


def _registry(monkeypatch, tmp_path, accounts):
    reg = tmp_path / "accounts.yaml"
    reg.write_text(yaml.safe_dump({"accounts": accounts}))
    monkeypatch.setattr(metrics, "REGISTRY", reg)


# ── the overlay: suffixed keys take over for one persona ────────

def test_overlay_applies_only_this_personas_keys(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "mara.bsky.social")
    monkeypatch.setenv("BLUESKY_HANDLE__JUNE", "junesplace.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD__JUNE", "pw-june")
    applied = credentials.overlay("june")
    assert os.environ["BLUESKY_HANDLE"] == "junesplace.bsky.social"
    assert os.environ["BLUESKY_APP_PASSWORD"] == "pw-june"
    assert set(applied) == {"BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"}


def test_overlay_for_the_other_persona_changes_nothing(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "mara.bsky.social")
    monkeypatch.setenv("BLUESKY_HANDLE__JUNE", "junesplace.bsky.social")
    assert credentials.overlay("mara") == []
    assert os.environ["BLUESKY_HANDLE"] == "mara.bsky.social"


# ── the binding: keys must prove which account they are ─────────

JUNE_BSKY = {"persona": "june", "platform": "bluesky",
             "handle": "junesplace.bsky.social"}


def test_another_accounts_handle_is_refused_naming_both(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "mara.bsky.social")
    err = credentials.binding_error("june", "bluesky", JUNE_BSKY)
    assert "mara.bsky.social" in err and "junesplace.bsky.social" in err
    assert "BLUESKY_HANDLE__JUNE" in err  # the fix is named, not implied


def test_matching_handle_passes_despite_at_sign_and_case(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "@JunesPlace.bsky.social")
    assert credentials.binding_error("june", "bluesky", JUNE_BSKY) == ""


def test_an_unset_identity_var_cannot_prove_the_account(monkeypatch):
    err = credentials.binding_error("june", "bluesky", JUNE_BSKY)
    assert "unset" in err and "junesplace.bsky.social" in err


def test_a_numeric_chat_id_is_not_comparable_to_a_handle(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHANNEL", "-1001234567890")
    acct = {"persona": "mara", "platform": "telegram", "handle": "marabrews"}
    assert credentials.binding_error("mara", "telegram", acct) == ""


def test_platforms_without_an_identity_var_are_left_to_suffix_discipline():
    acct = {"persona": "june", "platform": "mastodon", "handle": "june"}
    assert credentials.binding_error("june", "mastodon", acct) == ""


# ── the guard: one platform, two personas, independent verdicts ─

def _fleet(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    opened = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    _registry(monkeypatch, tmp_path, [
        {"persona": "mara", "platform": "bluesky",
         "handle": "mara.bsky.social", "opened_at": opened,
         "status": "suspended"},
        {"persona": "june", "platform": "bluesky",
         "handle": "junesplace.bsky.social", "opened_at": opened,
         "status": "active"},
    ])
    monkeypatch.setattr(guard, "platform_activity", lambda p, h: (0, None))
    return store.connect()


def test_two_bluesky_accounts_are_gated_independently(tmp_path, monkeypatch):
    """The scenario that forced persona-scoped guarding: Mara's Bluesky is
    suspended, June's is fresh — neither verdict may leak into the other."""
    con = _fleet(monkeypatch, tmp_path)
    monkeypatch.setenv("BLUESKY_HANDLE", "junesplace.bsky.social")
    ok_june, why_june = guard.can_post(con, "bluesky", persona_id="june")
    ok_mara, why_mara = guard.can_post(con, "bluesky", persona_id="mara")
    assert ok_june is True, why_june
    assert ok_mara is False and "suspended" in why_mara


def test_junes_cycle_with_maras_keys_is_stopped_at_the_gate(tmp_path, monkeypatch):
    con = _fleet(monkeypatch, tmp_path)
    monkeypatch.setenv("BLUESKY_HANDLE", "mara.bsky.social")
    ok, why = guard.can_post(con, "bluesky", persona_id="june")
    assert ok is False
    assert "another" in why and "junesplace.bsky.social" in why


def test_a_platform_the_persona_never_registered_is_blocked(tmp_path, monkeypatch):
    con = _fleet(monkeypatch, tmp_path)
    ok, why = guard.can_post(con, "telegram", persona_id="june")
    assert ok is False and "no row" in why and "accounts.yaml" in why


def test_registry_platforms_lists_only_that_personas_legs(tmp_path, monkeypatch):
    _fleet(monkeypatch, tmp_path)
    assert guard.registry_platforms("june") == ["bluesky"]
    assert guard.registry_platforms("nobody") == []
