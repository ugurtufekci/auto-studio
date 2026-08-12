"""Per-account credentials — which keys speak for which handle.

Environment variables are process-global, but accounts are per persona. While
every platform had exactly one account, bare names (BLUESKY_HANDLE, …) were
enough. The moment a second account exists on the same platform — June's
Bluesky next to Mara's — bare names become a cross-posting hazard: a June
cycle that finds Mara's BLUESKY_HANDLE would publish June's content into
Mara's account, the worst identity incident this studio can produce short of
a ban. Two mechanisms close that hole, both anchored to the fleet registry:

overlay(persona_id)
    Copies VAR__<PERSONA> over VAR for the life of this process, so
    per-account keys live side by side in one .env:

        BLUESKY_HANDLE__JUNE=junesplace.bsky.social
        BLUESKY_APP_PASSWORD__JUNE=xxxx-xxxx-xxxx-xxxx

    run.py applies the overlay right after resolving which persona speaks,
    before anything reads a credential.

binding_error(persona_id, platform)
    Publishing is refused unless the platform's identity-bearing variable
    (the handle the keys log into) MATCHES the registry handle for
    (persona, platform) — credentials must prove they belong to the account
    being published, never assumed to. Platforms whose credentials carry no
    handle (mastodon, youtube) cannot be proven this way; for those, keeping
    only suffixed vars per persona is the discipline.

A numeric identity value (a Telegram chat id like -100…) cannot be compared
to a registry handle and is allowed through — the registry stores handles,
and numeric ids are legitimate configuration.
"""

from __future__ import annotations

import os
import re

PLATFORM_VARS = {
    "bluesky": ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"),
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"),
    "instagram": ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_HANDLE"),
    "mastodon": ("MASTODON_INSTANCE", "MASTODON_TOKEN"),
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"),
}

# the variable whose value IS the account identity the keys authenticate as
IDENTITY_VAR = {
    "bluesky": "BLUESKY_HANDLE",
    "telegram": "TELEGRAM_CHANNEL",
    "instagram": "INSTAGRAM_HANDLE",
}


def _suffix(persona_id: str) -> str:
    return "__" + re.sub(r"[^A-Z0-9]", "", persona_id.upper())


def _norm(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def lookup(var: str, persona_id: str | None = None) -> str:
    """The value VAR effectively has for this persona: the suffixed name wins,
    the bare name is the fallback. This is exactly what overlay() will make
    true at publish time, so pre-run checks (the console) see the same world
    the run will."""
    if persona_id:
        value = os.environ.get(var + _suffix(persona_id))
        if value:
            return value
    return os.environ.get(var, "")


def overlay(persona_id: str) -> list[str]:
    """Apply this persona's suffixed credentials over the bare names for the
    rest of the process. Returns the bare names that were overridden, so the
    cycle log shows which identity the run is keyed to."""
    suffix = _suffix(persona_id)
    applied = []
    for var_names in PLATFORM_VARS.values():
        for var in var_names:
            value = os.environ.get(var + suffix)
            if value:
                os.environ[var] = value
                applied.append(var)
    return applied


def binding_error(persona_id: str, platform: str, acct: dict | None = None) -> str:
    """Empty string when the identity variable proves the right account;
    otherwise the reason publishing must not proceed. The caller usually has
    the registry row already and passes it; otherwise it is looked up."""
    var = IDENTITY_VAR.get(platform)
    if not var:
        return ""
    if acct is None:
        from studio import guard
        acct = guard.registry_account(platform, persona_id)
    if not acct:
        return ""  # the missing registry row is the guard's own message
    expected = _norm(str(acct.get("handle") or ""))
    if not expected:
        return ""
    actual = lookup(var, persona_id)
    if not actual:
        return (f"{var} is unset — cannot prove which {platform} account this "
                f"machine's keys belong to (registry expects "
                f"@{acct.get('handle')}); set {var} or "
                f"{var}{_suffix(persona_id)}")
    if actual.lstrip("-").isdigit():
        return ""
    if _norm(actual) != expected:
        return (f"{var}={actual} is not @{acct.get('handle')}, the registry "
                f"handle for persona '{persona_id}' — refusing to publish one "
                f"persona's content through another's account; set "
                f"{var}{_suffix(persona_id)} (and its companion keys) for "
                f"this persona")
    return ""
