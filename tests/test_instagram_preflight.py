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

# realistic lengths: the preflight rejects a short token as truncated
REAL_TOKEN = "IGAA" + "r" * 200
BAD_TOKEN = "IGAA" + "b" * 200
FB_TOKEN = "EAA" + "f" * 200


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


def test_a_wrong_user_id_is_caught_with_the_real_one(monkeypatch):
    """A plausible but wrong id — a second account's, or a mistyped one —
    is answered with the id to paste instead."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841777777777")
    _token_is(monkeypatch)
    problems = ig.preflight()
    assert len(problems) == 1
    assert "17841999" in problems[0]          # names the id to paste
    assert "athomewithjunecaprio" in problems[0]


def test_token_for_the_wrong_account_is_refused(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_HANDLE", "athomewithjunecaprio")
    _token_is(monkeypatch, username="someoneelse")
    problems = ig.preflight()
    assert any("different account" in p for p in problems)


def test_rejected_token_says_so_plainly(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", BAD_TOKEN)
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
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_HANDLE", "@AtHomeWithJuneCaprio")  # case/@ ok
    _token_is(monkeypatch)
    assert ig.preflight() == []


def test_token_prefix_picks_the_host_that_minted_it(monkeypatch):
    """The 'Failed to decrypt' class of bug: each Meta login flow answers
    only on its own host, so the token's own prefix routes the call."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    assert ig._api_base() == ig.API
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", FB_TOKEN)
    assert ig._api_base() == ig.API_FACEBOOK


def test_quotes_and_whitespace_around_the_token_are_stripped(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", '  "IGAApasted"\n')
    assert ig._token_state()["token"] == "IGAApasted"


def test_a_token_of_no_known_shape_is_called_out(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "9f3c" * 50)  # not a token
    problems = ig.preflight()
    assert len(problems) == 1
    assert "does not look like a Meta token" in problems[0]


def test_a_truncated_token_is_diagnosed_before_the_network(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGAA" + "x" * 40)
    monkeypatch.setattr(ig, "whoami", lambda: pytest.fail("never called"))
    problems = ig.preflight()
    assert len(problems) == 1 and "truncated" in problems[0]


def test_dead_basic_display_token_names_the_flow_to_use(monkeypatch):
    """IGQ… is the shut-down Basic Display API, which never could publish."""
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGQVJ" + "z" * 180)
    monkeypatch.setattr(ig, "whoami", lambda: pytest.fail("never called"))
    problems = ig.preflight()
    assert len(problems) == 1
    assert "Basic Display" in problems[0] and "IGAA" in problems[0]


def test_the_fingerprint_never_leaks_the_token(monkeypatch):
    secret = "IGAA" + "s3cr3t" * 40
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", secret)
    fp = ig.token_fingerprint()
    assert "s3cr3t" not in fp
    assert "IGAA" in fp and str(len(secret)) in fp


def test_facebook_token_walks_pages_to_the_instagram_account(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", FB_TOKEN)
    monkeypatch.setattr(ig, "_get_json", lambda url, params: (
        {"data": [{"name": "no-ig-page"},
                  {"name": "June's Page",
                   "instagram_business_account": {"id": "17841999",
                                                  "username": "junes"}}]}
        if "me/accounts" in url else {}))
    me = ig.whoami()
    assert me["user_id"] == "17841999" and me["username"] == "junes"
    assert "June's Page" in me["via"]


def test_facebook_token_without_a_linked_account_says_how_to_link(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", FB_TOKEN)
    monkeypatch.setattr(ig, "_get_json", lambda url, params: {"data": []})
    with pytest.raises(RuntimeError, match="must be Professional"):
        ig.whoami()


def test_facebook_refresh_needs_the_apps_own_credentials(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", FB_TOKEN)
    monkeypatch.delenv("INSTAGRAM_APP_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_APP_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="INSTAGRAM_APP_ID"):
        ig.refresh_token()


def test_a_missing_media_host_does_not_block_a_healthy_setup(monkeypatch):
    """Instagram fetches media by URL and a generated image already carries
    its provider's, so a missing media host is advice, not a blocker — a
    checker that cries NOT READY over a working setup gets ignored."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    _token_is(monkeypatch)
    monkeypatch.setattr(media_host, "configured", lambda: False)
    assert ig.preflight() == []
    assert any("media host" in a for a in ig.advisories())


def test_a_configured_media_host_says_nothing(monkeypatch):
    monkeypatch.setattr(media_host, "configured", lambda: True)
    assert ig.advisories() == []


def test_documentation_placeholders_are_named_as_such(monkeypatch):
    """The operator's real trap: .env filled from the docs' example shapes.
    Regenerating a token would not have helped — nothing was wrong yet."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "IGQ...")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")
    monkeypatch.setattr(ig, "whoami", lambda: pytest.fail("never called"))
    problems = ig.preflight()
    assert len(problems) == 1
    assert "INSTAGRAM_ACCESS_TOKEN" in problems[0]
    assert "INSTAGRAM_USER_ID" in problems[0]
    assert "example text" in problems[0] and ".env.example" in problems[0]


def test_angle_bracket_descriptions_count_as_placeholders(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "<the long token, starts IGAA>")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setattr(ig, "whoami", lambda: pytest.fail("never called"))
    assert "example text" in ig.preflight()[0]


def test_real_values_are_not_mistaken_for_placeholders(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setenv("INSTAGRAM_HANDLE", "athomewithjunecaprio")
    _token_is(monkeypatch)
    assert ig.preflight() == []


def test_meta_errors_carry_their_code_and_a_cause_to_check():
    """Meta's prose repeats across unrelated causes; the numbers next to it
    are what an operator can act on."""
    out = ig.describe_error({"error": {
        "message": "API access blocked.", "type": "OAuthException",
        "code": 200, "fbtrace_id": "Axyz"}})
    assert "code 200" in out and "trace Axyz" in out
    assert "content_publish" in out and "add it again" in out


def test_a_blocked_message_without_a_known_code_still_gets_a_checklist():
    out = ig.describe_error({"error": {"message": "API access blocked."}})
    assert "Professional" in out and "content_publish" in out


def test_expired_token_code_names_the_invalidation_trap():
    out = ig.describe_error({"error": {"message": "Session expired", "code": 190}})
    assert "generating a new token kills the previous one" in out


def test_describe_error_survives_a_bodyless_response():
    assert "no detail" in ig.describe_error({}, "")
    assert "gateway" in ig.describe_error({}, "502 bad gateway").lower()


def test_a_specific_api_diagnosis_is_not_buried_under_a_generic_one(monkeypatch):
    """When Meta already said why, the preflight must not append its own
    'your token is probably wrong' — that sends the operator to regenerate
    a token that was never the problem."""
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")

    def blocked():
        raise RuntimeError("the token was rejected: HTTP 400 API access "
                           "blocked · code 200 — the app is not allowed to "
                           "act for this account yet")

    monkeypatch.setattr(ig, "whoami", blocked)
    problems = ig.preflight()
    assert len(problems) == 1
    assert "not allowed to act" in problems[0]
    assert "generate a fresh one" not in problems[0]
    assert "starts 'IGAA'" in problems[0]      # fingerprint still travels


def test_an_opaque_failure_still_gets_the_generic_advice(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", REAL_TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841999")
    monkeypatch.setattr(ig, "whoami", lambda: (_ for _ in ()).throw(
        RuntimeError("connection reset")))
    assert "generate a fresh one" in ig.preflight()[0]
