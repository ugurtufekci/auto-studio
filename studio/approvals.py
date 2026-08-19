"""The approval queue — the operator's hand between a finished draft and
the platform.

Drafts live in the git ledger (studio/draftpool.py): the cycle that made
them may have run on another machine entirely. Release can come hours after
cycle time, so nothing stale is trusted:

- the guard runs AGAIN at release, with at_release=True — cadence, min-gap,
  registry status AND the credential binding are judged against this moment
  and this machine (the publishing machine is the one that must prove its
  keys). A refusal leaves the draft pending with the reason on it.
- media resolves in order: the committed ledger copy → the provider's own
  URL (checked alive; for upload platforms it is downloaded first). A
  provider URL that died is re-uploaded from the ledger copy when one
  exists, so approving three days late still works.

Rejection is terminal and cheap: the record moves to resolved/ with the
reason, nothing touches a platform.
"""

from __future__ import annotations

from pathlib import Path

from studio import credentials, deliver, draftpool, guard, ledger_git, progress, store

# platforms whose adapters UPLOAD bytes (need a local file); instagram
# instead hands Meta a URL to fetch
_UPLOADS_FILE = {"bluesky", "telegram", "mastodon", "youtube"}


def _url_alive(url: str) -> bool:
    import httpx
    try:
        return httpx.head(url, timeout=15,
                          follow_redirects=True).status_code == 200
    except Exception:
        return False


def _materialise(d: dict) -> tuple[str, dict]:
    """(local media path, provenance) ready for deliver.publish — or raises
    with what is missing named."""
    provenance = dict(d.get("provenance") or {})
    source_url = provenance.get("source_url") or ""
    local = draftpool.media_path(d)

    if local is None and source_url and d["platform"] in _UPLOADS_FILE:
        # upload platforms need bytes; fetch the provider's copy while it lives
        import httpx
        draftpool.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(source_url.split("?")[0]).suffix or ".jpg"
        local = draftpool.MEDIA_DIR / f"{d['id']}{suffix}"
        r = httpx.get(source_url, timeout=120, follow_redirects=True)
        r.raise_for_status()
        local.write_bytes(r.content)

    if source_url and not _url_alive(source_url):
        # expired provider URL: re-upload the ledger copy so URL-fetch
        # platforms (instagram) still have somewhere public to look
        if local is not None:
            try:
                import fal_client
                provenance["source_url"] = fal_client.upload_file(str(local))
                provenance["reuploaded"] = True
            except Exception:
                provenance["source_url"] = ""
        else:
            provenance["source_url"] = ""

    if local is None and not provenance.get("source_url"):
        raise RuntimeError(
            "draft has no usable media: the ledger copy is missing on this "
            "machine (git pull?) and the provider URL has expired")
    return str(local or ""), provenance


def approve(con, draft_id: str) -> dict:
    """Release one draft. Returns {ok, message, url?}; a guard refusal leaves
    the draft pending so the operator can release it when the gate opens."""
    d = draftpool.get(draft_id)
    if not d:
        return {"ok": False, "message": f"draft '{draft_id}' not found or already resolved"}

    ok, reason = guard.can_post(con, d["platform"], persona_id=d["persona"],
                                at_release=True)
    if not ok:
        return {"ok": False,
                "message": f"guard refused right now — draft stays pending: {reason}"}

    credentials.overlay(d["persona"])
    missing = [v for v in credentials.PLATFORM_VARS.get(d["platform"], ())
               if not credentials.lookup(v, d["persona"])]
    if missing:
        # unset keys are a machine problem, not a draft problem — same class
        # as a guard refusal, and the draft is NOT consumed
        return {"ok": False,
                "message": ("cannot release yet — this machine is missing "
                            f"{', '.join(missing)}; put them in .env and "
                            "press approve again (the draft stays pending)")}
    if d["platform"] == "instagram":
        # Meta answers a wrong id or a bad token with 'Failed to decrypt',
        # which names nothing an operator can act on — ask the token who it
        # is first and report the mismatch in words that say what to fix
        from studio import publisher_instagram
        try:
            problems = publisher_instagram.preflight()
        except Exception:
            problems = []       # never let the check itself block a release
        if problems:
            return {"ok": False,
                    "message": "cannot release yet — " + "; ".join(problems)}
    # Claimed here, after the free checks and before the first slow call. A
    # release takes a minute or more with nothing on screen, and on
    # 2026-08-18 the operator pressed approve again mid-flight and started a
    # second publish of the same carousel.
    busy = draftpool.begin_release(draft_id)
    if busy:
        return {"ok": False, "message": busy}
    # Bound, not wrapped: the console serves each request on its own thread
    # and every release binds before it reports, so a key never outlives the
    # action that set it.
    progress.bind(draft_id)
    progress.note("preparing the media")
    try:
        media, provenance = _materialise(d)
    except Exception as e:
        # a fixable condition (pull the repo, wait out a network blip) —
        # the draft is NOT consumed
        draftpool.end_release(draft_id)
        return {"ok": False, "message": f"cannot release yet: {str(e)[:200]}"}
    try:
        rendition = {"text": d.get("text", ""), "title": d.get("title", ""),
                     "tags": d.get("tags") or []}
        progress.note("sending it to " + d["platform"])
        result = deliver.publish(
            d["platform"], rendition, d.get("text", ""), media,
            d.get("media_kind", "image"), d.get("alt", ""), provenance,
            d["persona"], hero=(d.get("media_kind") == "video"),
            slides=[str(x) for x in draftpool.media_paths(d)])
    except Exception as e:
        # the attempt may have partially landed (a created-but-unpublished
        # container, a timeout after the API call) — so the draft stays
        # pending with the error on it, and the RETRY is the operator's
        # deliberate call after a glance at the account, never automatic
        draftpool.end_release(draft_id)
        draftpool.stamp_error(draft_id, str(e)[:300])
        return {"ok": False,
                "message": (f"publish failed — draft stays pending: "
                            f"{str(e)[:200]} — check the account before "
                            "retrying")}

    progress.note("published")
    draftpool.end_release(draft_id)
    store.save_post(con, int(d.get("brief_id") or 0), d["platform"],
                    result["uri"], result["url"],
                    d.get("title") or d.get("text", ""), "published")
    draftpool.resolve(draft_id, "approved", result["url"])
    carried = ledger_git.publish_decision(draft_id, "approved")
    return {"ok": True,
            "message": f"live on {d['platform']}"
                       + (f" · ledger {carried}" if carried else ""),
            "url": result["url"]}


def mark_posted_by_hand(con, draft_id: str, url: str = "") -> dict:
    """The operator published this one themselves — take it out of the queue.

    Nothing is sent anywhere. A reel needs a trending track chosen inside the
    Instagram app, and no API can add audio to a reel after it exists, so
    some drafts are always released by hand. Until now those sat in the
    queue for good, indistinguishable from work not yet done.

    The URL is optional but worth pasting: it is what makes the ledger able
    to answer "what went out and where is it?" for a hand-released post the
    same way it does for an approved one, and what lets performance be
    attributed back to the style that produced it."""
    d = draftpool.get(draft_id)
    if not d:
        return {"ok": False,
                "message": f"draft '{draft_id}' not found or already resolved"}
    url = (url or "").strip()
    if url and not url.startswith("http"):
        return {"ok": False,
                "message": "that does not look like a link — paste the post's "
                           "URL, or leave it empty"}
    if url:
        # the cadence guard reads local post history as well as the platform,
        # and a hand-released post is a real post: it has to count
        store.save_post(con, int(d.get("brief_id") or 0), d["platform"],
                        url, url, d.get("title") or d.get("text", ""),
                        "published")
    draftpool.resolve(draft_id, draftpool.POSTED_BY_HAND,
                      url or "posted by hand")
    carried = ledger_git.publish_decision(draft_id, draftpool.POSTED_BY_HAND)
    return {"ok": True,
            "message": ("marked as posted by hand — out of the queue"
                        + ("" if url else ", though without a link it cannot "
                                          "be matched to its numbers later")
                        + (f" · ledger {carried}" if carried else "")),
            "url": url}


def reject(con, draft_id: str, note: str = "") -> dict:
    """Turn a draft down. The note is the whole point: it is the only signal
    the studio ever gets back from the one person who reads every draft, and
    the next brief for this persona is written with it in hand."""
    d = draftpool.get(draft_id)
    if not d:
        return {"ok": False, "message": f"draft '{draft_id}' not found or already resolved"}
    note = (note or "").strip()
    draftpool.resolve(draft_id, "rejected", note or "rejected by operator")
    carried = ledger_git.publish_decision(draft_id, "rejected")
    learned = (" — the next brief for this persona will carry your reason"
               if note else "")
    return {"ok": True,
            "message": f"draft rejected{learned}"
                       + (f" · ledger {carried}" if carried else "")}
