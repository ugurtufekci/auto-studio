"""Mastodon adapter — a real social feed (profile, followers, boosts) with an
official bot flag and no app-review gate.

Token: Mastodon web UI → Settings → Development → New application
       scopes: write:statuses, write:media, write:accounts

.env:
  MASTODON_INSTANCE=https://mastodon.example
  MASTODON_TOKEN=...
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from studio.publisher import compose_plain

TEXT_LIMIT = 500          # instance default; most run 500
MEDIA_POLL_SECONDS = 90


def configured() -> bool:
    return bool(os.environ.get("MASTODON_TOKEN")
                and os.environ.get("MASTODON_INSTANCE"))


def _base() -> str:
    return os.environ["MASTODON_INSTANCE"].rstrip("/")


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['MASTODON_TOKEN']}"}


def _upload_media(path: str, alt: str) -> str:
    """Upload one attachment, waiting out server-side processing (202)."""
    with open(path, "rb") as f:
        r = httpx.post(f"{_base()}/api/v2/media", headers=_headers(),
                       files={"file": (Path(path).name, f)},
                       data={"description": alt[:1500]}, timeout=300)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"media upload failed: HTTP {r.status_code} {r.text[:150]}")
    media = r.json()
    mid = media["id"]
    if r.status_code == 202 or not media.get("url"):
        # still transcoding — poll until the server reports it ready
        deadline = time.time() + MEDIA_POLL_SECONDS
        while time.time() < deadline:
            time.sleep(3)
            chk = httpx.get(f"{_base()}/api/v1/media/{mid}",
                            headers=_headers(), timeout=60)
            if chk.status_code == 200 and (chk.json() or {}).get("url"):
                break
        else:
            raise RuntimeError("media still processing after "
                               f"{MEDIA_POLL_SECONDS}s")
    return mid


def _post_status(caption: str, media_ids: list[str],
                 provenance: dict | None = None,
                 persona_id: str | None = None) -> dict:
    payload = {"status": compose_plain(caption, TEXT_LIMIT, provenance, persona_id),
               "visibility": "public"}
    for i, mid in enumerate(media_ids):
        payload[f"media_ids[{i}]"] = mid
    r = httpx.post(f"{_base()}/api/v1/statuses", headers=_headers(),
                   data=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"status failed: HTTP {r.status_code} {r.text[:150]}")
    s = r.json()
    return {"uri": s.get("uri") or s.get("id"), "url": s.get("url", "")}


def post_image(caption: str, image_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    from studio.publisher import _alt_for
    mid = _upload_media(image_path, _alt_for(alt, "image", provenance))
    return _post_status(caption, [mid], provenance, persona_id)


def post_video(caption: str, video_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    from studio.publisher import _alt_for
    mid = _upload_media(video_path, _alt_for(alt, "video", provenance))
    return _post_status(caption, [mid], provenance, persona_id)


def mark_as_bot(bio: str = "") -> dict:
    """Set the official bot flag (and optionally the bio) — the platform's own
    mechanism for declaring labeled automation."""
    data = {"bot": "true", "discoverable": "true"}
    if bio:
        data["note"] = bio.strip()[:500]
    r = httpx.patch(f"{_base()}/api/v1/accounts/update_credentials",
                    headers=_headers(), data=data, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"profile update failed: HTTP {r.status_code} {r.text[:150]}")
    a = r.json()
    return {"acct": a.get("acct"), "bot": a.get("bot"), "url": a.get("url")}


def whoami() -> dict:
    r = httpx.get(f"{_base()}/api/v1/accounts/verify_credentials",
                  headers=_headers(), timeout=30)
    r.raise_for_status()
    a = r.json()
    return {"acct": a.get("acct"), "display_name": a.get("display_name"),
            "bot": a.get("bot"), "followers": a.get("followers_count"),
            "statuses": a.get("statuses_count"), "url": a.get("url")}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("not configured — set MASTODON_INSTANCE and MASTODON_TOKEN in .env")
    else:
        me = whoami()
        print(f"mastodon ok: @{me['acct']} · bot={me['bot']} · "
              f"{me['followers']} followers, {me['statuses']} posts")
        print(f"  profile: {me['url']}")
