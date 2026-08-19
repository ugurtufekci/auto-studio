"""Instagram adapter — the revenue platform, published through the Graph API.

Publishing to an account we own needs neither App Review nor Business
Verification: Meta's Standard Access covers "your Instagram professional
account or an account you manage". What it does need is a professional
account, a token, and — unlike every other adapter here — a public URL for
the media, because the API fetches rather than accepts uploads.

The flow is three calls, not one:

    POST /<ig-user-id>/media          create a container (returns creation_id)
    GET  /<creation_id>?fields=status_code   poll until FINISHED
    POST /<ig-user-id>/media_publish  publish the container

Images are usually FINISHED immediately; Reels are transcoded, so the poll is
not optional. Containers expire unpublished after 24 hours, and Meta asks
callers to poll about once a minute rather than tightly.

Every post carries `is_ai_generated=true`. Meta requires the label for
photorealistic video and realistic audio and auto-labels images it detects;
we set it on everything because the disclosure is the point, not the minimum.

.env (see .env.example — never copy the shapes below, they are not values):
  INSTAGRAM_USER_ID       the numeric id of the account the token belongs to
  INSTAGRAM_ACCESS_TOKEN  the long-lived token, ~150-300 characters
  plus a media host, see studio/media_host.py

Run `python -m studio.publisher_instagram` to check the keys before relying
on them; it names what is wrong instead of leaving it to a failed release.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import httpx

from studio import media_host, progress
from studio.publisher import compose_plain

API = "https://graph.instagram.com/v21.0"
API_FACEBOOK = "https://graph.facebook.com/v21.0"
CAPTION_LIMIT = 2200
MAX_HASHTAGS = 5   # IG refuses more at publish time (operator-verified 2026-08-14)

# Meta ships TWO ways to reach the same publishing endpoints, and a token
# from one is gibberish to the other — literally: the wrong host answers
# 'Failed to decrypt', because it cannot decrypt a token minted for its
# sibling. Which one an operator ends up with depends on which button they
# found in the app dashboard, so the adapter reads the token's own prefix
# and talks to the host that minted it.
#
#   IGAA… — "Instagram API with Instagram login"  → graph.instagram.com
#   EAA…  — "Instagram API with Facebook login"   → graph.facebook.com
#
# The publishing calls (/media, /media_publish) are identical on both; only
# the host, the identity lookup, and the refresh mechanics differ.


def _is_facebook_token(token: str = "") -> bool:
    return (token or _token_state().get("token", "")).startswith("EAA")


def _api_base() -> str:
    return API_FACEBOOK if _is_facebook_token() else API

# Meta's guidance is roughly one status check a minute; a still is normally
# ready on the first look and a Reel takes a few. Past this we stop rather
# than hold the cycle open indefinitely — the container stays valid for 24h,
# so a slow transcode is recoverable by hand, not lost.
POLL_INTERVAL_SECONDS = 20
POLL_MAX_SECONDS = 300


# ── token lifecycle ─────────────────────────────────────────────
# Instagram's long-lived token lasts 60 days. Nothing warns you when it
# lapses: posting simply starts failing, and an account that quietly stopped
# publishing is the kind of thing noticed weeks later. So the token is stored
# with its expiry, refreshed automatically inside the window, and reported to
# the console while it is still fixable. Meta requires a token to be at least
# 24 hours old before it can be refreshed, and an expired one cannot be
# refreshed at all — that needs the OAuth flow again, by hand.

TOKEN_FILE = Path(__file__).resolve().parent.parent / "store" / "instagram_token.json"
TOKEN_LIFETIME_DAYS = 60
REFRESH_WHEN_DAYS_LEFT = 10


def _token_state() -> dict:
    """Stored token wins over the env var: the env var is the bootstrap value,
    the file is what refreshing keeps current."""
    try:
        state = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if state.get("token"):
            return state
    except Exception:
        pass
    # strip quotes and stray whitespace: a token pasted into .env with
    # surrounding "…" or a trailing space fails with the same opaque
    # 'Failed to decrypt' as a wrong token
    env = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip().strip('"\'')
    return {"token": env, "expires_at": "", "source": "env"}


def _save_token_state(token: str, lifetime_seconds: int | None = None) -> dict:
    expires = datetime.now(UTC) + timedelta(
        seconds=lifetime_seconds or TOKEN_LIFETIME_DAYS * 86400)
    state = {"token": token, "expires_at": expires.isoformat(),
             "refreshed_at": datetime.now(UTC).isoformat(), "source": "refresh"}
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return state


def token_days_left() -> float | None:
    """Days until the token dies, or None when we have never seen an expiry
    (a bootstrap env token whose age we cannot know)."""
    expires = _token_state().get("expires_at")
    if not expires:
        return None
    try:
        return (datetime.fromisoformat(expires) - datetime.now(UTC)).total_seconds() / 86400
    except ValueError:
        return None


def refresh_token() -> dict:
    """Exchange the current long-lived token for a fresh 60 days. Each login
    flow renews its own way: Instagram tokens refresh themselves, Facebook
    ones are re-exchanged against the app's id and secret."""
    state = _token_state()
    if not state.get("token"):
        raise RuntimeError("no Instagram token to refresh")
    if _is_facebook_token(state["token"]):
        app_id = os.environ.get("INSTAGRAM_APP_ID", "")
        app_secret = os.environ.get("INSTAGRAM_APP_SECRET", "")
        if not (app_id and app_secret):
            raise RuntimeError(
                "a Facebook-login token is renewed with the app's own "
                "credentials — set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET "
                "in .env (Meta app dashboard → App settings → Basic), or "
                "generate a new token by hand every 60 days")
        r = httpx.get(f"{API_FACEBOOK}/oauth/access_token",
                      params={"grant_type": "fb_exchange_token",
                              "client_id": app_id, "client_secret": app_secret,
                              "fb_exchange_token": state["token"]}, timeout=30)
    else:
        r = httpx.get(f"{API}/refresh_access_token",
                      params={"grant_type": "ig_refresh_token",
                              "access_token": state["token"]}, timeout=30)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if r.status_code != 200 or not body.get("access_token"):
        err = (body.get("error") or {}).get("message") or r.text[:160]
        raise RuntimeError(
            f"instagram token refresh failed: HTTP {r.status_code} {err}. "
            "An expired token cannot be refreshed — re-run the OAuth flow and "
            "put the new long-lived token in INSTAGRAM_ACCESS_TOKEN.")
    return _save_token_state(body["access_token"], body.get("expires_in"))


def refresh_if_due() -> str | None:
    """Called before publishing. Returns a message when it acted or when the
    operator needs to, None when there is nothing to say."""
    left = token_days_left()
    if left is None:
        return None
    if left <= 0:
        return ("instagram token EXPIRED — refreshing is no longer possible; "
                "re-run the OAuth flow and set INSTAGRAM_ACCESS_TOKEN")
    if left <= REFRESH_WHEN_DAYS_LEFT:
        try:
            state = refresh_token()
            return (f"instagram token refreshed — valid again until "
                    f"{state['expires_at'][:10]}")
        except Exception as e:
            return f"instagram token refresh FAILED with {left:.0f} days left: {str(e)[:120]}"
    return None


def configured() -> bool:
    return bool(os.environ.get("INSTAGRAM_USER_ID") and _token_state().get("token"))


def _user() -> str:
    return os.environ["INSTAGRAM_USER_ID"]


def _token() -> str:
    return _token_state()["token"]


def describe_error(body: dict, text: str = "") -> str:
    """Meta's prose is short and its meaning lives in the numbers beside it:
    the same 'API access blocked.' covers a personal account, an app whose
    permissions were never added, and a restricted app. So the code and
    subcode travel with the message, and the causes worth checking are
    spelled out for the ones an operator actually hits."""
    err = (body or {}).get("error") or {}
    message = err.get("message") or (text or "")[:160] or "no detail"
    code = err.get("code")
    sub = err.get("error_subcode")
    parts = [message.rstrip(".")]
    if code is not None:
        parts.append(f"code {code}" + (f"/{sub}" if sub else ""))
    if err.get("fbtrace_id"):
        parts.append(f"trace {err['fbtrace_id']}")
    line = " · ".join(parts)
    # the SUBCODE is the specific one; the code alone is a category ("100 ·
    # Invalid parameter" covers everything you could get wrong at once)
    hint = (_SUBCODE_HINTS.get(str(sub))
            or _ERROR_HINTS.get(str(code)) or _hint_for_message(message))
    return f"{line}{' — ' + hint if hint else ''}"


# Subcodes we have actually hit, with what each one turned out to mean.
# Meta's own message names no parameter and no cause, which sends an
# operator looking at the account, the token and the images — none of which
# were the problem either time.
_SUBCODE_HINTS = {
    "2207100": "a container was sent a parameter it does not accept. Every "
               "time so far this was is_ai_generated on a CAROUSEL CHILD — "
               "it is a top-level-only flag and belongs on the parent. Fixed "
               "in studio/publisher_instagram.py on 2026-08-18; if you are "
               "seeing it after pulling that fix, the console is still "
               "running the old code — stop it and start it again, because "
               "git pull does not reload a running process",
    "2207003": "Meta could not download the media from its URL — check the "
               "URL is publicly reachable (studio/media_host.py)",
    "2207026": "the video format is not one Reels accepts",
    "2207057": "the aspect ratio is outside what this media type allows",
}


_ERROR_HINTS = {
    "190": "the token is expired or was invalidated (generating a new token "
           "kills the previous one) — generate a fresh one",
    "10": "the app is missing the permission this call needs: in the Meta "
          "app dashboard open the Instagram use case and add "
          "instagram_business_content_publish, then generate the token again",
    "200": "the app is not allowed to act for this account yet. In the Meta "
           "app dashboard: Instagram → API setup with Instagram login → "
           "check the use case lists instagram_business_content_publish "
           "(not only _basic), then REMOVE the account and add it again so "
           "it grants the new permission, and generate a fresh token. "
           "Permissions added after an account was linked do not apply "
           "retroactively — this is the usual cause",
    "4": "you have hit Meta's rate limit — wait an hour and retry",
}


def _hint_for_message(message: str) -> str:
    m = message.lower()
    if "access blocked" in m or "blocked" in m:
        return ("Meta is refusing the app itself, not the post. Check, in "
                "order: (1) the Instagram account is Professional/Creator, "
                "not personal; (2) in the app dashboard the Instagram use "
                "case lists instagram_business_basic AND "
                "instagram_business_content_publish; (3) the account appears "
                "under 'API setup with Instagram login' with those "
                "permissions approved — if you added it before adding the "
                "permissions, remove it and add it again; (4) the app is not "
                "restricted (App dashboard → the alert banner at the top)")
    if "professional" in m or "business account" in m:
        return ("the account is still personal — switch it to a "
                "Professional/Creator account in the Instagram app")
    if "not supported for reel" in m:
        return ("a REELS container was sent a parameter that only images "
                "take — alt_text was the one, fixed in "
                "studio/publisher_instagram.py on 2026-08-19. If you are "
                "seeing it after pulling that fix, the console is still "
                "running the old code — stop it and start it again, because "
                "git pull does not reload a running process")
    return ""


def _call(method: str, path: str, params: dict) -> dict:
    fn = httpx.post if method == "POST" else httpx.get
    r = fn(f"{_api_base()}/{path}",
           params={**params, "access_token": _token()}, timeout=120)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if r.status_code != 200 or "error" in body:
        # WHICH parameters were sent, and which build sent them. Meta names
        # neither, and without both an operator and I spent three rounds
        # arguing about whether a fix had reached the running process — a
        # `git pull` does not reload a module already in memory, and nothing
        # in the old message could tell us either way.
        keys = ",".join(sorted(k for k in params if k != "access_token"))
        raise RuntimeError(f"instagram {method} {path}: HTTP {r.status_code} "
                           f"{describe_error(body, r.text)} "
                           f"[sent: {keys} · build {_build()}]")
    return body


@lru_cache(maxsize=1)
def _build() -> str:
    """A short fingerprint of THIS file as it is running.

    Printed with every API failure so "did the fix reach you?" is a fact in
    the error text rather than a question over three messages."""
    import hashlib

    try:
        return hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest()[:8]
    except Exception:
        return "unknown"


def _get_json(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, timeout=30)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if r.status_code != 200 or "error" in body:
        raise RuntimeError(f"the token was rejected: HTTP {r.status_code} "
                           f"{describe_error(body, r.text)}")
    return body


def whoami() -> dict:
    """Ask the token who it is — the one call that can tell an operator
    whether the id they pasted is even the right account, because it needs
    no id of its own. Returns {user_id, username}.

    Instagram-login tokens answer directly. Facebook-login tokens speak for
    a person, not an account, so the Instagram account hangs off one of
    their Pages — /me/accounts walks to it."""
    token = _token()
    if _is_facebook_token(token):
        body = _get_json(f"{API_FACEBOOK}/me/accounts",
                         {"fields": "name,instagram_business_account{id,username}",
                          "access_token": token})
        for page in body.get("data") or []:
            ig = page.get("instagram_business_account") or {}
            if ig.get("id"):
                return {"user_id": str(ig["id"]),
                        "username": str(ig.get("username") or ""),
                        "via": f"facebook page '{page.get('name', '')}'"}
        raise RuntimeError(
            "this Facebook token reaches no Instagram account: the account "
            "must be Professional AND linked to a Facebook Page the token "
            "can see (Instagram app → Settings → Sharing to other apps → "
            "Facebook), then generate the token again")
    body = _get_json(f"{API}/me",
                     {"fields": "user_id,username", "access_token": token})
    return {"user_id": str(body.get("user_id") or ""),
            "username": str(body.get("username") or ""), "via": "instagram login"}


def token_fingerprint() -> str:
    """Enough of the token's shape to diagnose it, none of its secret: a
    truncated paste and a wrong-flow token look identical in an error
    message otherwise, and an operator should never have to send the value
    itself to anyone to get help."""
    tok = _token_state().get("token", "")
    if not tok:
        return "no token set"
    return f"starts '{tok[:4]}', {len(tok)} characters"


# Values that LOOK like credentials but are documentation: an ellipsis, an
# angle-bracket description, or the round example id. Naming them as
# placeholders is the difference between "fix your .env" and an hour spent
# regenerating a token that was never the problem.
def _is_placeholder(value: str) -> bool:
    v = value.strip()
    return (not v or v.endswith("...") or v.startswith("<") or v.endswith(">")
            or v in {"17841400000000000", "IGQ...", "IGAA...", "EAA...",
                     "changeme", "xxx", "your-token-here"})


def preflight() -> list[str]:
    """Everything that must be true before a release can work, checked in the
    order a human would fix it. Returns human-readable problems, empty when
    the account is ready to publish. Never raises."""
    problems = []
    uid = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    tok = _token_state().get("token", "")
    placeholders = [name for name, value in
                    (("INSTAGRAM_ACCESS_TOKEN", tok),
                     ("INSTAGRAM_USER_ID", uid),
                     ("INSTAGRAM_HANDLE",
                      os.environ.get("INSTAGRAM_HANDLE", "")))
                    if value and _is_placeholder(value)]
    if placeholders:
        return [f"{', '.join(placeholders)} still hold example text from the "
                "docs, not real values — the Meta setup has not been done "
                "yet. See .env.example for the four steps; nothing here is "
                "broken, the keys simply do not exist yet"]
    if not tok:
        problems.append("INSTAGRAM_ACCESS_TOKEN is empty — generate a token "
                        "in the Meta app dashboard and paste it into .env")
    if not uid:
        problems.append("INSTAGRAM_USER_ID is empty — .env needs the numeric "
                        "id of the account the token belongs to")
    if not tok:
        return problems
    if tok.startswith("IGQ"):
        problems.append(
            f"this is a Basic Display token ({token_fingerprint()}) — that "
            "API was shut down and could never publish anyway. In the Meta "
            "app dashboard open 'Instagram → API setup with Instagram "
            "login' and generate a token there; it will start IGAA")
        return problems
    if not tok.startswith(("IGAA", "EAA")):
        problems.append(
            f"INSTAGRAM_ACCESS_TOKEN does not look like a Meta token "
            f"({token_fingerprint()}). An Instagram-login token starts IGAA, "
            "a Facebook-login one starts EAA — copy the whole value, it is "
            "very long and easy to truncate")
        return problems
    if len(tok) < 100:
        problems.append(
            f"the token looks truncated ({token_fingerprint()}) — a real one "
            "is roughly 150-300 characters. Copy the full value: the "
            "dashboard field shows only part of it, so use its copy button "
            "rather than selecting the text by hand")
        return problems
    try:
        me = whoami()
    except Exception as e:
        # when the API already told us WHY, repeating a generic "your token
        # is probably wrong" sends the operator to fix the wrong thing
        detail = str(e)
        already_explained = " — " in detail
        problems.append(
            f"{detail} ({token_fingerprint()})" if already_explained else
            f"{detail} ({token_fingerprint()}) — the token is wrong, already "
            "replaced by a newer one, or expired; generate a fresh one and "
            "paste it whole")
        return problems
    if uid and me["user_id"] and uid != me["user_id"]:
        problems.append(
            f"INSTAGRAM_USER_ID is {uid}, but this token belongs to "
            f"@{me['username']} whose id is {me['user_id']} — put "
            f"{me['user_id']} in .env")
    expected = os.environ.get("INSTAGRAM_HANDLE", "").strip().lstrip("@").lower()
    if expected and me["username"] and expected != me["username"].lower():
        problems.append(
            f"INSTAGRAM_HANDLE says @{expected}, but the token authenticates "
            f"as @{me['username']} — these keys belong to a different account")
    return problems


def advisories() -> list[str]:
    """Things worth knowing that do NOT block a release. Kept apart from
    preflight() on purpose: a checker that reports a healthy setup as NOT
    READY teaches the operator to ignore it. Instagram fetches media by URL,
    and a generated image already carries its provider's — a media host only
    matters for drafts whose media exists solely on this machine."""
    notes = []
    if not media_host.configured():
        notes.append("no public media host configured — fine for drafts whose "
                     "media still has a live provider URL (the usual case); "
                     "set MEDIA_HOST only if a release ever fails on an "
                     "expired one (see studio/media_host.py)")
    return notes


def publishing_limit() -> dict:
    """What Meta says we have left in the rolling 24h window.

    Their docs give two different ceilings (50 and 100) on the same page, so
    the live number is the only one worth trusting."""
    body = _call("GET", f"{_user()}/content_publishing_limit",
                 {"fields": "config,quota_usage"})
    row = (body.get("data") or [{}])[0]
    return {"used": row.get("quota_usage"),
            "total": (row.get("config") or {}).get("quota_total")}


def _create_container(media_url: str, caption: str, is_video: bool,
                      alt: str = "", carousel_item: bool = False) -> str:
    params = {
        "caption": caption,
        "is_ai_generated": "true",   # disclosed by default, never by calculation
    }
    if alt:
        params["alt_text"] = alt[:1000]
    if carousel_item:
        # A child carries no caption of its own — the parent holds it, and
        # sending one here is silently dropped.
        params.pop("caption")
        # is_ai_generated is a TOP-LEVEL flag and a child container rejects
        # it outright: HTTP 400, code 100 / subcode 2207100, "Invalid
        # parameter", with nothing in the message naming the parameter.
        # Bisected against the live API on 2026-08-18 — the same call
        # succeeds the moment this key is dropped, with or without alt_text.
        # It is why no carousel had ever published through the console; every
        # one of them was posted by hand instead.
        #
        # The disclosure is not lost by removing it here. It belongs on the
        # parent, which is the object the feed shows, and post_carousel sets
        # it there — along with the 🤖 line inside the caption itself.
        params.pop("is_ai_generated", None)
        params["is_carousel_item"] = "true"
    if is_video:
        params["media_type"] = "REELS"
        params["video_url"] = media_url
        # A REELS container rejects alt_text outright: HTTP 400, code 100,
        # "The param alt_text is not supported for REEL". Unlike the
        # carousel-child case this one names the parameter, so there is
        # nothing to bisect — Instagram simply has no alt text on a reel at
        # creation time. It is set in the app afterwards, or not at all.
        # Dropped here rather than never built, because the same alt text is
        # what the carousel twin publishes with and what the draft stores.
        params.pop("alt_text", None)
    else:
        params["image_url"] = media_url
    body = _call("POST", f"{_user()}/media", params)
    creation_id = body.get("id")
    if not creation_id:
        raise RuntimeError(f"instagram returned no container id: {body}")
    return creation_id


def _await_container(creation_id: str) -> None:
    """Block until Meta finishes ingesting the media, or say why it did not."""
    deadline = time.time() + POLL_MAX_SECONDS
    last = ""
    while time.time() < deadline:
        body = _call("GET", creation_id, {"fields": "status_code,status"})
        last = body.get("status_code") or ""
        if last == "FINISHED":
            return
        if last in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                f"instagram container {last}: {body.get('status') or 'no detail'}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"instagram container still '{last or 'IN_PROGRESS'}' after "
        f"{POLL_MAX_SECONDS}s — it stays valid for 24h, so publish it by hand "
        f"rather than re-rendering (creation_id {creation_id})")


def _publish(creation_id: str) -> dict:
    body = _call("POST", f"{_user()}/media_publish", {"creation_id": creation_id})
    media_id = body.get("id") or ""
    permalink = ""
    try:
        permalink = _call("GET", media_id, {"fields": "permalink"}).get("permalink", "")
    except Exception:
        pass  # published fine; only the pretty URL is missing
    handle = os.environ.get("INSTAGRAM_HANDLE", "")
    return {"uri": f"ig:{media_id}",
            "url": permalink or (f"https://www.instagram.com/{handle}/" if handle else "")}


def _post(media_path: str, caption: str, alt: str, is_video: bool,
          provenance: dict | None, persona_id: str | None) -> dict:
    note = refresh_if_due()
    if note:
        print(f"  [instagram] {note}")
    text = compose_plain(caption, CAPTION_LIMIT, provenance, persona_id, max_hashtags=MAX_HASHTAGS)
    # the factory records the provider's own URL for a generated render; when
    # there is one, Meta can fetch straight from it
    progress.note("uploading the media", 1, 3)
    media_url = media_host.publish(media_path, (provenance or {}).get("source_url", ""))
    creation_id = _create_container(media_url, text, is_video, alt)
    progress.note("waiting for Instagram to accept it"
                  + (" — a reel is transcoded, which takes a few minutes"
                     if is_video else ""), 2, 3)
    _await_container(creation_id)
    progress.note("publishing", 3, 3)
    return _publish(creation_id)


CAROUSEL_MAX = 10          # Meta's ceiling for children in one post


def post_carousel(caption: str, image_paths: list[str], alt: str = "",
                  provenance: dict | None = None,
                  persona_id: str | None = None) -> dict:
    """A carousel: one child container per slide, then a parent that holds
    the caption and the disclosure.

    The AI disclosure goes on the PARENT, which is what the feed shows and
    what a viewer reads — a child carries no caption at all, so putting it
    there would publish an undisclosed post."""
    paths = [p for p in image_paths if p][:CAROUSEL_MAX]
    if len(paths) < 2:
        raise ValueError("a carousel needs at least two slides")
    note = refresh_if_due()
    if note:
        print(f"  [instagram] {note}")
    text = compose_plain(caption, CAPTION_LIMIT, provenance, persona_id,
                         max_hashtags=MAX_HASHTAGS)
    children = []
    for i, path in enumerate(paths):
        progress.note(f"uploading slide {i + 1} of {len(paths)}",
                      i + 1, len(paths) + 2)
        url = media_host.publish(path, "")
        cid = _create_container(url, "", False, alt if i == 0 else "",
                                carousel_item=True)
        _await_container(cid)
        children.append(cid)
        print(f"  [instagram] slide {i + 1}/{len(paths)} ready")
    progress.note("assembling the carousel", len(paths) + 1, len(paths) + 2)
    parent = _call("POST", f"{_user()}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": text,
        "is_ai_generated": "true",
    }).get("id")
    if not parent:
        raise RuntimeError("instagram returned no carousel container id")
    _await_container(parent)
    return _publish(parent)


def post_image(caption: str, image_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    return _post(image_path, caption, alt, False, provenance, persona_id)


def post_video(caption: str, video_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    return _post(video_path, caption, alt, True, provenance, persona_id)


if __name__ == "__main__":
    from pathlib import Path as _P

    from dotenv import load_dotenv
    load_dotenv(_P(__file__).resolve().parent.parent / ".env",
                encoding="utf-8-sig")
    from studio import version as _version
    print(_version.banner())
    print("checking the Instagram keys in .env …\n")
    problems = preflight()
    if problems:
        print("NOT READY TO PUBLISH:")
        for p in problems:
            print(f"  · {p}")
        raise SystemExit(1)
    me = whoami()
    print(f"account ok: @{me['username']} (id {me['user_id']})")
    try:
        print("publishing limit:", publishing_limit())
    except Exception as e:
        print("publishing limit unavailable:", str(e)[:120])
    left = token_days_left()
    print("token:", f"{left:.0f} days left" if left is not None
          else "expiry unknown (bootstrap token — refresh once to start the clock)")
    for note in advisories():
        print(f"note: {note}")
    print("\nready — approve the draft in the console")
