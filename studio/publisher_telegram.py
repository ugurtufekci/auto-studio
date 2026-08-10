"""Telegram channel adapter — the zero-ban-risk platform.

Telegram bots are first-class, labeled automation: created via BotFather,
posting to the persona's own channel. No warm-up theatre needed — but the
same disclosure gate applies (compose_plain appends it mechanically).

.env:
  TELEGRAM_BOT_TOKEN=123456:ABC-...     (from @BotFather /newbot)
  TELEGRAM_CHANNEL=@marabrews           (bot must be channel admin)
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from studio.publisher import compose_plain

CAPTION_LIMIT = 1024  # telegram media-caption limit


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")
                and os.environ.get("TELEGRAM_CHANNEL"))


def _call(method: str, data: dict, files: dict | None = None) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = httpx.post(f"https://api.telegram.org/bot{token}/{method}",
                   data=data, files=files, timeout=180)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"telegram {method}: {body.get('description', r.text[:120])}")
    return body["result"]


def _result(msg: dict) -> dict:
    chan = os.environ["TELEGRAM_CHANNEL"]
    mid = msg["message_id"]
    return {"uri": f"tg:{chan}/{mid}",
            "url": f"https://t.me/{chan.lstrip('@')}/{mid}"}


def post_image(caption: str, image_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    with open(image_path, "rb") as f:
        msg = _call("sendPhoto",
                    data={"chat_id": os.environ["TELEGRAM_CHANNEL"],
                          "caption": compose_plain(caption, CAPTION_LIMIT, provenance, persona_id)},
                    files={"photo": (Path(image_path).name, f, "image/jpeg")})
    return _result(msg)


def post_video(caption: str, video_path: str, alt: str = "",
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    with open(video_path, "rb") as f:
        msg = _call("sendVideo",
                    data={"chat_id": os.environ["TELEGRAM_CHANNEL"],
                          "caption": compose_plain(caption, CAPTION_LIMIT, provenance, persona_id),
                          "supports_streaming": True},
                    files={"video": (Path(video_path).name, f, "video/mp4")})
    return _result(msg)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL in .env")
    else:
        me = _call("getMe", data={})
        print(f"bot ok: @{me['username']} → channel {os.environ['TELEGRAM_CHANNEL']}")
