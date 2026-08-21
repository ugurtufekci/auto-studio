"""One transient container ERROR must not cost the operator a release.

On 2026-08-20 a release failed with "instagram container ERROR: ERROR" — no
detail, nothing wrong with the account or the file. The next day the exact
same media was re-containered as a probe (created and polled, never
published): the original file FINISHED, a re-muxed copy FINISHED. Meta's
ingest simply drops a container now and then. An errored container is dead
and cannot be re-polled, so the correct insurance is one automatic retry
with a FRESH container for the same URL — the operator never sees the
hiccup. A second ERROR in a row is real and must surface, saying that the
retry already happened.
"""

import pytest

from studio import publisher_instagram as ig


def _erroring_await(dead: set):
    """An _await_container stand-in: containers listed in `dead` ERROR."""
    seen = []

    def fake(cid):
        seen.append(cid)
        if cid in dead:
            raise RuntimeError("instagram container ERROR: ERROR")

    fake.seen = seen
    return fake


def test_one_transient_error_is_absorbed_by_a_fresh_container(monkeypatch):
    made = []
    monkeypatch.setattr(ig, "_await_container", _erroring_await({"c1"}))

    def create():
        made.append(f"c{len(made) + 1}")
        return made[-1]

    assert ig._ingest(create, "reel") == "c2"
    assert made == ["c1", "c2"]        # the retry is a NEW container


def test_a_second_error_surfaces_and_says_the_retry_already_happened(monkeypatch):
    """Without this, the operator's natural next move is to click release
    again — which is exactly the retry that already ran and failed."""
    made = []
    monkeypatch.setattr(ig, "_await_container",
                        _erroring_await({"c1", "c2"}))

    def create():
        made.append(f"c{len(made) + 1}")
        return made[-1]

    with pytest.raises(RuntimeError) as e:
        ig._ingest(create, "reel")
    assert made == ["c1", "c2"]
    assert "twice in a row" in str(e.value)
    assert "instagram container ERROR" in str(e.value)


@pytest.mark.parametrize("message", [
    # EXPIRED means the container sat for 24h — a fresh one changes nothing
    # about why, and retrying hides a real scheduling problem.
    "instagram container EXPIRED: no detail",
    # a timeout leaves a LIVE container that the message tells the operator
    # to publish by hand; silently abandoning it for a new one would orphan
    # that advice.
    "instagram container still 'IN_PROGRESS' after 600s — it stays valid "
    "for 24h, so publish it by hand rather than re-rendering (creation_id c1)",
])
def test_only_the_error_status_is_retried(monkeypatch, message):
    made = []

    def fake_await(cid):
        raise RuntimeError(message)

    monkeypatch.setattr(ig, "_await_container", fake_await)

    def create():
        made.append(f"c{len(made) + 1}")
        return made[-1]

    with pytest.raises(RuntimeError) as e:
        ig._ingest(create, "image")
    assert made == ["c1"]              # no second container
    assert str(e.value) == message     # surfaced untouched


def test_a_create_failure_is_not_mistaken_for_an_ingest_failure(monkeypatch):
    """HTTP errors from container CREATION carry their own hints and must
    pass through once, unretried — only the ingest-status ERROR is known
    to be transient."""
    monkeypatch.setattr(ig, "_await_container",
                        lambda cid: pytest.fail("await must not run"))
    calls = []

    def create():
        calls.append(1)
        raise RuntimeError("instagram: HTTP 400 — Invalid parameter")

    with pytest.raises(RuntimeError, match="Invalid parameter"):
        ig._ingest(create, "image")
    assert len(calls) == 1


def test_a_reel_release_survives_one_transient_error_end_to_end(monkeypatch, tmp_path):
    """The wiring test: through _post itself, first container ERRORs, the
    automatic second one lands, and the publish uses the second id."""
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"not really a video")

    monkeypatch.setattr(ig, "refresh_if_due", lambda: "")
    monkeypatch.setattr(ig.media_host, "publish",
                        lambda p, s="": "https://host/reel.mp4")
    made = []
    monkeypatch.setattr(
        ig, "_create_container",
        lambda url, cap, vid, alt="", carousel_item=False:
            made.append({"url": url, "vid": vid}) or f"c{len(made)}")
    monkeypatch.setattr(ig, "_await_container", _erroring_await({"c1"}))
    published = []
    monkeypatch.setattr(ig, "_publish",
                        lambda cid: published.append(cid) or
                        {"uri": "ig:9", "url": "https://insta/p/9"})

    out = ig.post_video("caption", str(src))
    assert out["uri"] == "ig:9"
    assert published == ["c2"]
    assert made[0] == made[1]          # identical media, fresh container


def test_a_carousel_slide_survives_one_transient_error_end_to_end(monkeypatch, tmp_path):
    """Same insurance on every child and on the parent. Slide 1's first
    container dies; its retry must repeat the SAME url and the SAME alt
    text (the alt lives only on slide 1)."""
    monkeypatch.setattr(ig, "refresh_if_due", lambda: "")
    monkeypatch.setattr(ig.media_host, "publish",
                        lambda p, s="": f"https://host/{p}")
    made = []
    monkeypatch.setattr(
        ig, "_create_container",
        lambda url, cap, vid, alt="", carousel_item=False:
            made.append({"url": url, "alt": alt}) or f"c{len(made)}")
    monkeypatch.setattr(ig, "_await_container", _erroring_await({"c1"}))
    monkeypatch.setattr(ig, "_user", lambda: "123")
    parents = []
    monkeypatch.setattr(ig, "_call",
                        lambda m, p, params: parents.append(params) or {"id": "p1"})
    published = []
    monkeypatch.setattr(ig, "_publish",
                        lambda cid: published.append(cid) or
                        {"uri": "ig:9", "url": ""})

    ig.post_carousel("caption", ["s1.jpg", "s2.jpg"], alt="board alt")
    assert made[0] == made[1] == {"url": "https://host/s1.jpg",
                                  "alt": "board alt"}
    assert made[2] == {"url": "https://host/s2.jpg", "alt": ""}
    assert parents[-1]["children"] == "c2,c3"     # the dead c1 is not a child
    assert published == ["p1"]
