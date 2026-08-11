"""The approval queue — the operator's hand between a finished draft and
the platform.

A draft is everything a publish call needs, frozen at cycle time. Approval
can come hours later, so nothing from cycle time is trusted stale:

- the guard runs AGAIN at release time — cadence, min-gap, registry status
  and credential binding are judged against now, not against when the cycle
  ran. A refusal keeps the draft pending with the reason on it, it does not
  consume it.
- a provider-hosted media URL (fal's own copy) may have expired; if it no
  longer answers, the same local bytes are re-uploaded to the provider's
  storage so Instagram still has a public URL to fetch — same pixels,
  fresh address.

Rejection is terminal and cheap: the media stays on disk under assets/,
only the release is refused.
"""

from __future__ import annotations

from studio import credentials, deliver, guard, store


def _fresh_source_url(provenance: dict, media_path: str) -> dict:
    """Return provenance whose source_url is known to answer, re-uploading
    the local file to provider storage when the original has expired. On any
    doubt the URL is dropped — media_host then decides, loudly, whether it
    can serve the file another way."""
    url = (provenance or {}).get("source_url") or ""
    if not url:
        return provenance or {}
    import httpx
    try:
        alive = httpx.head(url, timeout=15, follow_redirects=True).status_code == 200
    except Exception:
        alive = False
    if alive:
        return provenance
    out = dict(provenance)
    try:
        import fal_client
        out["source_url"] = fal_client.upload_file(media_path)
        out["reuploaded"] = True
    except Exception:
        out["source_url"] = ""  # media_host raises with the fix named
    return out


def approve(con, draft_id: int) -> dict:
    """Release one draft. Returns {ok, message, url?}; a guard refusal leaves
    the draft pending so the operator can release it when the gate opens."""
    d = store.get_draft(con, draft_id)
    if not d:
        return {"ok": False, "message": f"draft #{draft_id} not found"}
    if d["status"] != "pending":
        return {"ok": False, "message": f"draft #{draft_id} is already {d['status']}"}

    ok, reason = guard.can_post(con, d["platform"], persona_id=d["persona"])
    if not ok:
        store.resolve_draft(con, draft_id, "pending", f"held: {reason}")
        return {"ok": False,
                "message": f"guard refused right now — draft stays pending: {reason}"}

    credentials.overlay(d["persona"])
    provenance = _fresh_source_url(d["provenance"], d["media_path"])
    rendition = {"text": d["text"], "title": d["title"], "tags": d["tags"]}
    try:
        result = deliver.publish(
            d["platform"], rendition, d["text"], d["media_path"],
            d["media_kind"], d["alt"], provenance, d["persona"],
            hero=(d["media_kind"] == "video"))
    except Exception as e:
        store.resolve_draft(con, draft_id, "failed", str(e)[:300])
        return {"ok": False, "message": f"publish failed: {str(e)[:200]}"}

    store.save_post(con, d["brief_id"], d["platform"], result["uri"],
                    result["url"], d["title"] or d["text"], "published")
    store.resolve_draft(con, draft_id, "approved", result["url"])
    return {"ok": True, "message": f"live on {d['platform']}",
            "url": result["url"]}


def reject(con, draft_id: int, note: str = "") -> dict:
    d = store.get_draft(con, draft_id)
    if not d:
        return {"ok": False, "message": f"draft #{draft_id} not found"}
    if d["status"] != "pending":
        return {"ok": False, "message": f"draft #{draft_id} is already {d['status']}"}
    store.resolve_draft(con, draft_id, "rejected", note or "rejected by operator")
    return {"ok": True, "message": f"draft #{draft_id} rejected"}
