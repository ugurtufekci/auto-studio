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
from pathlib import Path

import httpx

from studio import media_host
from studio.publisher import compose_plain

API = "https://graph.instagram.com/v21.0"
API_FACEBOOK = "https://graph.facebook.com/v21.0"
CAPTION_LIMIT = 2200

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
        err = (body.get("error") or {}).get("message") or r.text[:160]
        raise RuntimeError(f"instagram {method} {path}: HTTP {r.status_code} {err}")
    return body


def _get_json(url: str, params: dict) -> dict:
    r = httpx.get(url, params=params, timeout=30)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if r.status_code != 200 or "error" in body:
        err = (body.get("error") or {}).get("message") or r.text[:160]
        raise RuntimeError(f"the token was rejected: HTTP {r.status_code} {err}")
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
        problems.append(f"{e} ({token_fingerprint()}) — the token is wrong, "
                        "already replaced by a newer one, or expired; "
                        "generate a fresh one and paste it whole")
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
    if not media_host.configured():
        problems.append("no public media host configured — Instagram fetches "
                        "media by URL; see studio/media_host.py (a generated "
                        "image's provider URL works while it lives)")
    return problems


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
                      alt: str = "") -> str:
    params = {
        "caption": caption,
        "is_ai_generated": "true",   # disclosed by default, never by calculation
    }
    if alt:
        params["alt_text"] = alt[:1000]
    if is_video:
        params["media_type"] = "REELS"
        params["video_url"] = media_url
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
    text = compose_plain(caption, CAPTION_LIMIT, provenance, persona_id)
    # the factory records the provider's own URL for a generated render; when
    # there is one, Meta can fetch straight from it
    media_url = media_host.publish(media_path, (provenance or {}).get("source_url", ""))
    creation_id = _create_container(media_url, text, is_video, alt)
    _await_container(creation_id)
    return _publish(creation_id)


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
    print("\nready — approve the draft in the console")
