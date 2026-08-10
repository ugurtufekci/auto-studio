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

.env:
  INSTAGRAM_USER_ID=17841400000000000
  INSTAGRAM_ACCESS_TOKEN=IGQ...        (long-lived, 60 days — refresh it)
  plus a media host, see studio/media_host.py
"""

from __future__ import annotations

import os
import time

import httpx

from studio import media_host
from studio.publisher import compose_plain

API = "https://graph.instagram.com/v21.0"
CAPTION_LIMIT = 2200

# Meta's guidance is roughly one status check a minute; a still is normally
# ready on the first look and a Reel takes a few. Past this we stop rather
# than hold the cycle open indefinitely — the container stays valid for 24h,
# so a slow transcode is recoverable by hand, not lost.
POLL_INTERVAL_SECONDS = 20
POLL_MAX_SECONDS = 300


def configured() -> bool:
    return bool(os.environ.get("INSTAGRAM_USER_ID")
                and os.environ.get("INSTAGRAM_ACCESS_TOKEN"))


def _user() -> str:
    return os.environ["INSTAGRAM_USER_ID"]


def _token() -> str:
    return os.environ["INSTAGRAM_ACCESS_TOKEN"]


def _call(method: str, path: str, params: dict) -> dict:
    fn = httpx.post if method == "POST" else httpx.get
    r = fn(f"{API}/{path}", params={**params, "access_token": _token()}, timeout=120)
    body = {}
    try:
        body = r.json()
    except Exception:
        pass
    if r.status_code != 200 or "error" in body:
        err = (body.get("error") or {}).get("message") or r.text[:160]
        raise RuntimeError(f"instagram {method} {path}: HTTP {r.status_code} {err}")
    return body


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
    text = compose_plain(caption, CAPTION_LIMIT, provenance, persona_id)
    media_url = media_host.publish(media_path)
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
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("not configured — set INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN")
    elif not media_host.configured():
        print("token ok, but no public media host — Instagram fetches media by "
              "URL; see studio/media_host.py")
    else:
        me = _call("GET", _user(), {"fields": "username,followers_count"})
        print(f"account ok: @{me.get('username')} · "
              f"{me.get('followers_count')} followers")
        print("publishing limit:", publishing_limit())
