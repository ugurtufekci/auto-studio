"""Platform dispatch — one door through which anything gets published.

Both callers use this and only this: the cycle (run.py) when an account
publishes directly, and the approval queue (studio/approvals.py) when the
operator releases a held draft. One switch means one place where a platform
can be wired wrong — and the approval path can never drift from what the
cycle would have done.
"""

from __future__ import annotations


def publish(platform: str, rendition: dict, caption_fallback: str,
            media: str, media_kind: str, alt: str,
            provenance: dict | None, persona_id: str | None,
            hero: bool = False, slides: list[str] | None = None) -> dict:
    """Publish one asset to one platform. Returns {"uri", "url"}; raises with
    the missing setting named when an adapter is not configured."""
    r = rendition or {}
    text = r.get("text") or caption_fallback
    if media_kind == "carousel" and platform != "instagram":
        raise RuntimeError(
            f"{platform} has no carousel: publishing this draft here would "
            f"post only its first slide. Release it on instagram, or make a "
            f"single-image draft for {platform}.")

    if platform == "bluesky":
        from studio import publisher
        client = publisher.login()   # only authenticated touch per cycle
        fn = publisher.post_video if media_kind == "video" else publisher.post_image
        return fn(client, text, media, alt, provenance, persona_id)

    if platform == "telegram":
        from studio import publisher_telegram as tg
        if not tg.configured():
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL not set")
        fn = tg.post_video if media_kind == "video" else tg.post_image
        return fn(text, media, alt, provenance, persona_id)

    if platform == "instagram":
        from studio import publisher_instagram as ig
        if not ig.configured():
            raise RuntimeError("INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN not set")
        if media_kind == "carousel":
            if not slides or len(slides) < 2:
                raise RuntimeError(
                    "a carousel draft reached publish with fewer than two "
                    "slides on this machine — run `git pull` so the ledger "
                    "media is here, or release it by hand")
            return ig.post_carousel(text, slides, alt, provenance, persona_id)
        fn = ig.post_video if media_kind == "video" else ig.post_image
        return fn(text, media, alt, provenance, persona_id)

    if platform == "mastodon":
        from studio import publisher_mastodon as masto
        if not masto.configured():
            raise RuntimeError("MASTODON_INSTANCE / MASTODON_TOKEN not set")
        fn = masto.post_video if media_kind == "video" else masto.post_image
        return fn(text, media, alt, provenance, persona_id)

    if platform == "youtube":
        from studio import publisher_youtube as yt
        if not yt.configured():
            raise RuntimeError("YOUTUBE_* credentials not set "
                               "(see scripts/youtube_auth.py)")
        # YouTube demonetises mass-produced, template-built content by name —
        # "slideshows with no narrative" is in the policy text. The hero clip
        # is the only cut that carries a shot, so it is the only cut allowed.
        if not hero:
            raise RuntimeError(
                "youtube takes hero clips only — a stills slideshow is exactly "
                "the mass-produced shape YouTube's inauthentic content policy "
                "demonetises. Run with --hero.")
        if media_kind != "video":
            raise RuntimeError("youtube needs a video — run with --hero")
        return yt.post_video(media, r.get("title", caption_fallback),
                             r.get("text") or r.get("description", ""),
                             r.get("tags", []), provenance, persona_id)

    raise RuntimeError(f"no adapter for platform '{platform}'")
