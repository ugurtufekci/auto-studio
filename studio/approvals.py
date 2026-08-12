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

from studio import credentials, deliver, draftpool, guard, store

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
    try:
        media, provenance = _materialise(d)
    except Exception as e:
        # a fixable condition (pull the repo, wait out a network blip) —
        # the draft is NOT consumed
        return {"ok": False, "message": f"cannot release yet: {str(e)[:200]}"}
    try:
        rendition = {"text": d.get("text", ""), "title": d.get("title", ""),
                     "tags": d.get("tags") or []}
        result = deliver.publish(
            d["platform"], rendition, d.get("text", ""), media,
            d.get("media_kind", "image"), d.get("alt", ""), provenance,
            d["persona"], hero=(d.get("media_kind") == "video"))
    except Exception as e:
        # the attempt may have partially landed (a created-but-unpublished
        # container, a timeout after the API call) — so the draft stays
        # pending with the error on it, and the RETRY is the operator's
        # deliberate call after a glance at the account, never automatic
        draftpool.stamp_error(draft_id, str(e)[:300])
        return {"ok": False,
                "message": (f"publish failed — draft stays pending: "
                            f"{str(e)[:200]} — check the account before "
                            "retrying")}

    store.save_post(con, int(d.get("brief_id") or 0), d["platform"],
                    result["uri"], result["url"],
                    d.get("title") or d.get("text", ""), "published")
    draftpool.resolve(draft_id, "approved", result["url"])
    return {"ok": True, "message": f"live on {d['platform']}",
            "url": result["url"]}


def reject(con, draft_id: str, note: str = "") -> dict:
    d = draftpool.get(draft_id)
    if not d:
        return {"ok": False, "message": f"draft '{draft_id}' not found or already resolved"}
    draftpool.resolve(draft_id, "rejected", note or "rejected by operator")
    return {"ok": True, "message": f"draft {draft_id} rejected"}
