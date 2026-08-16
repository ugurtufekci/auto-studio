"""Replies to comments on our own posts — drafted here, sent by a human.

A comment answered within the hour is worth more than one answered in a
day: it doubles the comment count, it tells the feed the post is alive, and
it is how an account stops being a broadcast. What it must never become is
automation — `never_automate: comments` is in the platform policy for a
reason, and this module respects it exactly: it writes a draft and stops.
The operator reads it, edits it if they like, and posts it themselves.

This is deliberately the OPPOSITE surface from commenting on strangers'
posts, which the studio does not do. Answering someone who came to us is
hospitality; arriving uninvited at scale is spam, and platforms read it
that way whoever writes the words.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "replies"
PENDING_DIR = DATA_DIR / "pending"
DONE_DIR = DATA_DIR / "done"

PROMPT = """You write a REPLY, as "{name}" — {tagline}.

Her voice:
- register: {register}
- rhythm: {rhythm}
- emoji: {emoji}
- NEVER: {never}

Someone commented on her post.

The post said: "{caption}"
{author} commented: "{comment}"

Write her reply. Rules that decide whether this is worth sending:
- Answer the ACTUAL comment. If it asks something, answer it plainly; if it
  praises, take it lightly and add one specific thing about the room rather
  than thanking and stopping.
- One or two sentences. A reply is not a caption.
- She is an openly-AI character: she never claims to have stood in the room,
  owned it, built it or visited it. She noticed it, imagined it, chose it.
- No hashtags, no bait, no "DM me", no asking for a follow.
- Never repeat the caption back.
- If the comment is hostile, spam, or asks for something we cannot give,
  reply with an empty string — silence is a legitimate answer and better
  than a bad reply.

Return STRICT JSON, no fences: {{"reply": "the text, or empty to stay silent",
 "why": "six words on the choice"}}"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40]


def handled_ids() -> set[str]:
    """Comment ids already drafted for, in either tray — so a refresh never
    drafts the same comment twice or resurrects one the operator dismissed."""
    ids = set()
    for d in (PENDING_DIR, DONE_DIR):
        for path in d.glob("*.json") if d.exists() else []:
            try:
                ids.add(json.loads(path.read_text(encoding="utf-8")).get("comment_id"))
            except Exception:
                continue
    return {i for i in ids if i}


def fetch_threads(persona_id: str, limit_posts: int = 6) -> list[dict]:
    """Recent comments on this persona's own Instagram posts.

    Reads only what the account can see about itself — the same access the
    console already uses to publish."""
    from studio import publisher_instagram as ig

    if not ig.configured():
        raise RuntimeError("INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN not set")
    me = ig.whoami().get("username", "")
    media = ig._call("GET", f"{ig._user()}/media", {
        "fields": "id,caption,permalink,timestamp,comments_count",
        "limit": limit_posts})
    threads = []
    for post in media.get("data") or []:
        if not post.get("comments_count"):
            continue
        body = ig._call("GET", f"{post['id']}/comments",
                        {"fields": "id,text,username,timestamp", "limit": 25})
        for c in body.get("data") or []:
            # our own replies come back as comments too; answering ourselves
            # would be a loop, not a conversation
            if c.get("username") and c["username"].lower() == me.lower():
                continue
            threads.append({
                "comment_id": c.get("id"), "comment": c.get("text", ""),
                "author": c.get("username", ""), "at": c.get("timestamp", ""),
                "post_url": post.get("permalink", ""),
                "caption": (post.get("caption") or "")[:400],
            })
    return threads


def draft_reply(thread: dict, persona_id: str, model: str | None = None) -> dict:
    """One reply in the persona's voice. Empty text means: stay silent."""
    from studio import llm, persona

    p = persona.load(persona_id)
    ident, voice = p["identity"], p["voice"]
    prompt = PROMPT.format(
        name=ident["name"], tagline=ident["tagline"],
        register=voice["register"], rhythm=voice["sentence_rhythm"],
        emoji=voice["emoji_policy"],
        never="; ".join(voice["never_says"]),
        caption=thread.get("caption", "")[:300],
        author=thread.get("author", "someone"),
        comment=thread.get("comment", ""))
    out = llm.extract_json(llm.complete(prompt, model=model, max_tokens=400))
    return {"reply": str(out.get("reply") or "").strip(),
            "why": str(out.get("why") or "").strip()}


def refresh(persona_id: str, model: str | None = None) -> list[dict]:
    """Draft a reply for every comment not seen before. Returns what was
    written — an empty list is the normal state of a quiet account."""
    from studio import style

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    known = handled_ids()
    written = []
    for thread in fetch_threads(persona_id):
        if not thread.get("comment_id") or thread["comment_id"] in known:
            continue
        drafted = draft_reply(thread, persona_id, model)
        if not drafted["reply"]:
            continue      # the brain chose silence; nothing to queue
        # the voice contract applies to replies too — the same linter that
        # keeps bait out of captions keeps it out of conversation
        problems = style.caption_problems(drafted["reply"], persona_id)
        record = {**thread, **drafted, "persona": persona_id,
                  "id": f"{_slug(thread['author'])}-{uuid.uuid4().hex[:6]}",
                  "voice_problems": problems, "status": "pending",
                  "created_at": _now()}
        (PENDING_DIR / f"{record['id']}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        written.append(record)
    return written


def pending() -> list[dict]:
    if not PENDING_DIR.exists():
        return []
    out = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(out, key=lambda r: r.get("at", ""), reverse=True)


def resolve(reply_id: str, status: str = "sent") -> bool:
    """Move a draft out of the tray once the operator has dealt with it."""
    src = PENDING_DIR / f"{reply_id}.json"
    if not src.exists():
        return False
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    record = json.loads(src.read_text(encoding="utf-8"))
    record.update(status=status, resolved_at=_now())
    (DONE_DIR / src.name).write_text(json.dumps(record, indent=2), encoding="utf-8")
    src.unlink()
    return True
