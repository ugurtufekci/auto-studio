"""Publisher — handbook pages 10/12, miniature.

Owns the Bluesky session and the pre-publish gate:
  - disclosure suffix appended MECHANICALLY to every post (cannot be skipped
    by any upstream step — the handbook's "identity & disclosure gates every
    publish" invariant)
  - 300-grapheme limit enforced by truncating the caption core, never the
    disclosure
  - images compressed under Bluesky's ~1MB blob limit
  - hashtags converted to real facets so they are clickable
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path

from atproto import Client, client_utils

from studio.brain import load_persona

MAX_GRAPHEMES = 300
IMG_BYTE_LIMIT = 950_000
STORE_DIR = Path(__file__).resolve().parent.parent / "store"
SESSION_FILE = STORE_DIR / "bsky_session.txt"
LOGIN_LOG = STORE_DIR / "bsky_logins.json"

# Bluesky rate-limits createSession far more tightly than posting (30 per 5
# minutes, 300 per day per account) and a maintainer has confirmed that
# repeated authentication is one of the shapes their anti-spam heuristics key
# on — separately from the published limits. A healthy cycle needs one login
# a day at most, so anything approaching these numbers is a malfunction (crash
# loop, unwritable session file) rather than legitimate use. Stopping loudly
# beats quietly hammering the endpoint that costs accounts.
MAX_LOGINS_PER_HOUR = 5
MAX_LOGINS_PER_DAY = 20


def _login_budget_check() -> list[float]:
    """Recent full-login timestamps, after pruning. Raises if the budget is
    spent — the caller must not fall back to logging in anyway."""
    import json
    import time

    now = time.time()
    try:
        stamps = [float(t) for t in json.loads(LOGIN_LOG.read_text(encoding="utf-8"))]
    except Exception:
        stamps = []
    stamps = [t for t in stamps if now - t < 86400]
    last_hour = sum(1 for t in stamps if now - t < 3600)
    if last_hour >= MAX_LOGINS_PER_HOUR or len(stamps) >= MAX_LOGINS_PER_DAY:
        raise RuntimeError(
            f"bluesky login budget exhausted ({last_hour} in the last hour, "
            f"{len(stamps)} in 24h). A healthy cycle logs in at most once a day, "
            f"so this means the session file is not persisting or a caller is "
            f"looping. Fix that before retrying — repeated authentication is a "
            f"documented suspension trigger. Reset by deleting {LOGIN_LOG}.")
    return stamps


def _record_login(stamps: list[float]) -> None:
    import json
    import time

    STORE_DIR.mkdir(exist_ok=True)
    LOGIN_LOG.write_text(json.dumps(stamps + [time.time()]), encoding="utf-8")


def login() -> Client:
    """Reuse the persisted session; a fresh createSession happens only when the
    saved one is missing or expired. (Repeated logins from scripts were part of
    the pattern that got the account flagged — sessions are cheap, keep them.)"""
    client = Client()
    if SESSION_FILE.exists():
        try:
            client.login(session_string=SESSION_FILE.read_text(encoding="utf-8").strip())
            SESSION_FILE.write_text(client.export_session_string(), encoding="utf-8")
            return client
        except Exception:
            pass  # expired/invalid — fall through to a real login
    stamps = _login_budget_check()
    client.login(os.environ["BLUESKY_HANDLE"], os.environ["BLUESKY_APP_PASSWORD"])
    _record_login(stamps)
    STORE_DIR.mkdir(exist_ok=True)
    SESSION_FILE.write_text(client.export_session_string(), encoding="utf-8")
    SESSION_FILE.chmod(0o600)
    return client


# ── text building ───────────────────────────────────────────────

def _alt_for(alt: str, kind: str, provenance: dict | None = None) -> str:
    """Accessibility text carries the same honest provenance tag."""
    model = ((provenance or {}).get("model") or "")
    tag = "stock photo" if model.startswith("pexels:") else f"AI-generated {kind}"
    return f"{alt} ({tag})"


def disclosure_for(provenance: dict | None = None,
                   persona_id: str | None = None) -> str:
    """The disclosure line is DERIVED FROM THE ASSET'S PROVENANCE, never
    hardcoded. A generated image says so; a licensed stock photo credits its
    photographer instead — claiming a real photographer's work is AI-generated
    would be a false statement, and the disclosure is the one thing in this
    system that must always be true.

    The wording is also per-persona, so the caller says which character is
    speaking. Publishing one persona's disclosure under another's name would
    break the same invariant from the other end."""
    persona = load_persona(persona_id)
    model = ((provenance or {}).get("model") or "")
    if model.startswith("pexels:"):
        credit = (provenance or {}).get("credit") or {}
        who = credit.get("photographer") or model.split(":", 1)[1] or "Pexels"
        return f"📷 Photo: {who} / Pexels · text by AI"
    return persona["identity"]["post_disclosure"].strip()


# A hashtag ends at the first character outside letters, digits and
# underscore — on Instagram, Telegram and Bluesky alike. The persona brain
# sometimes writes a multi-word tag with a separator (#kitchen-materials,
# #autumn_light), and a hyphenated one then links as its first word with
# the rest left as dead text: "#kitchen" + "-materials". The operator hit
# it live on 2026-08-21, pasting a caption for a hand-post. Underscored
# tags do stay clickable, but every deliberate tag in the voice is words
# run together (#interiordesign), so all separators collapse to that form.
_TAG_TOKEN = re.compile(r"( ?)#(\w+(?:[-.]\w+)*)")


def normalise_hashtags(text: str) -> str:
    """Collapse separators inside each hashtag and drop exact repeats a
    collapse may create (#stained-glass next to #stainedglass would read
    bot-like as a doubled tag). Runs before the cap is counted, so a
    hyphenated tag is trimmed as one tag, not cut at its hyphen."""
    seen: set[str] = set()

    def one(m: re.Match) -> str:
        tag = re.sub(r"[-._]", "", m.group(2))
        key = tag.casefold()
        if key in seen:
            return ""            # the duplicate goes, with its leading space
        seen.add(key)
        return f"{m.group(1)}#{tag}"

    return _TAG_TOKEN.sub(one, text)


def _trim_hashtags(text: str, keep: int) -> str:
    """Drop trailing hashtags beyond the platform's cap. Instagram rejects
    captions past its hashtag limit AT PUBLISH TIME (the operator met the
    wall by hand on 2026-08-14), so the ceiling is enforced here where every
    adapter and the console preview share it."""
    tags = re.findall(r"#\w+", text)
    for tag in tags[keep:]:
        text = text.replace(" " + tag, "", 1) if " " + tag in text \
            else text.replace(tag, "", 1)
    return re.sub(r"[ \t]+(\n|$)", r"\1", text).rstrip()


# "i" is a typo in English, not a lowercase aesthetic. A persona written in
# a quiet lowercase register applies that register to the pronoun too, and it
# reads as carelessness to every reader who knows the language — so this is
# fixed mechanically at the one place every platform's text passes through,
# rather than asked for in a prompt and hoped for.
_LOWER_I = re.compile(r"\bi\b(?=$|[^\w#])")
_LOWER_I_CONTRACTION = re.compile(r"\bi(?=['’](?:m|ve|ll|d)\b)")


def capitalise_pronoun(text: str) -> str:
    text = _LOWER_I_CONTRACTION.sub("I", text)
    return _LOWER_I.sub("I", text)


def compose_plain(caption: str, limit: int = MAX_GRAPHEMES,
                  provenance: dict | None = None,
                  persona_id: str | None = None,
                  max_hashtags: int | None = None) -> str:
    """Caption + mechanical disclosure suffix as plain text. The disclosure is
    never truncated — the caption core is. Shared by every platform adapter."""
    caption = normalise_hashtags(caption)
    if max_hashtags is not None:
        caption = _trim_hashtags(caption, max_hashtags)
    disclosure = disclosure_for(provenance, persona_id)
    budget = limit - len(disclosure) - 2
    caption = capitalise_pronoun(caption.strip())
    if len(caption) > budget:
        caption = caption[:budget - 1].rstrip() + "…"
    return f"{caption}\n{disclosure}"


def _compose(caption: str, provenance: dict | None = None,
             persona_id: str | None = None) -> client_utils.TextBuilder:
    """Bluesky variant: composed text with hashtags as clickable facets."""
    text = compose_plain(caption, MAX_GRAPHEMES, provenance, persona_id)
    tb = client_utils.TextBuilder()
    pos = 0
    for m in re.finditer(r"#(\w+)", text):
        if m.start() > pos:
            tb.text(text[pos:m.start()])
        tb.tag(m.group(0), m.group(1))
        pos = m.end()
    if pos < len(text):
        tb.text(text[pos:])
    return tb


# ── media prep ──────────────────────────────────────────────────

def _compress_image(path: str) -> bytes:
    from PIL import Image
    data = Path(path).read_bytes()
    if len(data) <= IMG_BYTE_LIMIT:
        return data
    img = Image.open(path).convert("RGB")
    for quality in (90, 82, 74, 66, 58):
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        if buf.tell() <= IMG_BYTE_LIMIT:
            return buf.getvalue()
    return buf.getvalue()


# ── publish ─────────────────────────────────────────────────────

def post_image(client: Client, caption: str, image_path: str, alt: str,
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    resp = client.send_image(
        text=_compose(caption, provenance, persona_id),
        image=_compress_image(image_path),
        image_alt=_alt_for(alt, "image", provenance),
    )
    return _as_result(resp)


def post_video(client: Client, caption: str, video_path: str, alt: str,
               provenance: dict | None = None,
               persona_id: str | None = None) -> dict:
    resp = client.send_video(
        text=_compose(caption, provenance, persona_id),
        video=Path(video_path).read_bytes(),
        video_alt=_alt_for(alt, "video", provenance),
    )
    return _as_result(resp)


def _as_result(resp) -> dict:
    handle = os.environ["BLUESKY_HANDLE"]
    rkey = resp.uri.split("/")[-1]
    return {"uri": resp.uri, "url": f"https://bsky.app/profile/{handle}/post/{rkey}"}


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    c = login()
    print("login ok:", c.me.handle)
    tb = _compose("testing the compose path #espresso #citymornings")
    print("composed text:", repr(tb.build_text()))
