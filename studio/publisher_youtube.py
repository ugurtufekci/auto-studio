"""YouTube adapter — uploads Shorts via the Data API v3.

⚠ HARD PLATFORM CONSTRAINT, not a bug in this code:
   Videos uploaded by an API client that has NOT passed Google's API compliance
   audit are locked to PRIVATE visibility. You cannot flip them public until the
   audit is granted (a formal review that takes weeks). Until then this adapter
   works end to end — the video lands in YouTube Studio — but nobody outside the
   account can watch it. `YOUTUBE_PRIVACY` lets you request public once audited.

Auth: OAuth2 refresh token (one-time consent via scripts/youtube_auth.py).

.env:
  YOUTUBE_CLIENT_ID=...
  YOUTUBE_CLIENT_SECRET=...
  YOUTUBE_REFRESH_TOKEN=...
  YOUTUBE_PRIVACY=private        # private | unlisted | public (public needs audit)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CATEGORY_LIFESTYLE = "22"   # "People & Blogs"


def configured() -> bool:
    return all(os.environ.get(k) for k in
               ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))


def _access_token() -> str:
    r = httpx.post(TOKEN_URL, data={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"token refresh failed: {r.text[:180]}")
    return r.json()["access_token"]


def post_video(video_path: str, title: str, description: str,
               tags: list[str] | None = None,
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    """Resumable-less simple upload (our clips are a few MB at most)."""
    from studio import persona as persona_cfg
    from studio.publisher import disclosure_for
    persona = persona_cfg.load(persona_id)
    disclosure = disclosure_for(provenance, persona_id)
    stock = ((provenance or {}).get("model") or "").startswith("pexels:")
    tail = disclosure if stock else f"{disclosure} — {persona['identity']['disclosure'].strip()}"
    # disclosure is appended mechanically here too — same invariant as every
    # other platform, adapted to YouTube's description field
    body_desc = f"{description.strip()}\n\n{tail}"

    privacy = os.environ.get("YOUTUBE_PRIVACY", "private")
    meta = {
        "snippet": {
            "title": title.strip()[:100],
            "description": body_desc[:5000],
            "tags": (tags or [])[:15],
            "categoryId": CATEGORY_LIFESTYLE,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # YouTube's own synthetic-media declaration
            "containsSyntheticMedia": not stock,
        },
    }

    token = _access_token()
    data = Path(video_path).read_bytes()
    files = {
        "metadata": (None, json.dumps(meta), "application/json"),
        "video": (Path(video_path).name, data, "video/mp4"),
    }
    r = httpx.post(UPLOAD_URL,
                   params={"part": "snippet,status", "uploadType": "multipart"},
                   headers={"Authorization": f"Bearer {token}"},
                   files=files, timeout=900)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"upload failed: HTTP {r.status_code} {r.text[:200]}")
    v = r.json()
    vid = v["id"]
    actual = v.get("status", {}).get("privacyStatus", privacy)
    if actual == "private":
        print("  [youtube] uploaded as PRIVATE — unaudited API clients cannot "
              "publish publicly (see module docstring)")
    return {"uri": f"youtube:{vid}", "url": f"https://youtu.be/{vid}",
            "privacy": actual}


def whoami() -> dict:
    token = _access_token()
    r = httpx.get("https://www.googleapis.com/youtube/v3/channels",
                  params={"part": "snippet,statistics", "mine": "true"},
                  headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        return {"error": "no channel on this account"}
    c = items[0]
    return {"channel": c["snippet"]["title"], "id": c["id"],
            "subscribers": c["statistics"].get("subscriberCount"),
            "videos": c["statistics"].get("videoCount")}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("not configured — run scripts/youtube_auth.py to get a refresh token")
    else:
        print("youtube ok:", whoami())
        print("privacy setting:", os.environ.get("YOUTUBE_PRIVACY", "private"))
