#!/usr/bin/env python
"""One-time YouTube OAuth consent → refresh token for .env.

Prereqs (you do these in the browser, ~10 min):
  1. https://console.cloud.google.com → new project
  2. APIs & Services → Library → enable "YouTube Data API v3"
  3. APIs & Services → OAuth consent screen → External → add yourself as a
     Test user (no Google verification needed while in Testing)
  4. Credentials → Create credentials → OAuth client ID → type "Desktop app"
  5. Copy the client id + secret into .env as YOUTUBE_CLIENT_ID / _SECRET

Then run:  python scripts/youtube_auth.py
It prints a URL, you approve in the browser, paste the code back, and it prints
the refresh token to put in .env.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"   # manual copy/paste flow


def main() -> int:
    cid = os.environ.get("YOUTUBE_CLIENT_ID")
    secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not cid or not secret:
        print("set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first "
              "(see this file's docstring)")
        return 1

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
    })
    print("\n1. Open this URL and approve access for the persona's YouTube account:\n")
    print(auth_url)
    print("\n2. Google shows you a code. Paste it here.")
    code = input("\ncode: ").strip()

    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    }, timeout=60)
    if r.status_code != 200:
        print("exchange failed:", r.text[:300])
        return 1
    tok = r.json()
    rt = tok.get("refresh_token")
    if not rt:
        print("no refresh_token returned — re-run (prompt=consent is required)")
        return 1
    print("\n✓ add this line to .env:\n")
    print(f"YOUTUBE_REFRESH_TOKEN={rt}")
    print("\nthen verify with:  python -m studio.publisher_youtube")
    return 0


if __name__ == "__main__":
    sys.exit(main())
