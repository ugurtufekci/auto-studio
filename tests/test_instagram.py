"""Instagram adapter tests — offline, against a faked Graph API.

Instagram is the only platform that fetches media instead of accepting an
upload, and the only one whose publish is three calls rather than one. Both
facts are easy to get subtly wrong in ways that fail deep inside Meta's
polling, where the error text says nothing useful — so the sequence, the
disclosure flag and the failure paths are pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import media_host  # noqa: E402
from studio import publisher_instagram as ig  # noqa: E402


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1784100")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(ig, "POLL_INTERVAL_SECONDS", 0)


class FakeGraph:
    """Records every call and answers like the Graph API does."""

    def __init__(self, statuses=("FINISHED",)):
        self.calls: list[tuple[str, str, dict]] = []
        self.statuses = list(statuses)

    def __call__(self, method, path, params):
        self.calls.append((method, path, params))
        if path.endswith("/media"):
            return {"id": "CONTAINER1"}
        if path.endswith("/media_publish"):
            return {"id": "MEDIA9"}
        if path == "CONTAINER1":
            return {"status_code": self.statuses.pop(0) if len(self.statuses) > 1
                    else self.statuses[0]}
        if path == "MEDIA9":
            return {"permalink": "https://www.instagram.com/p/abc/"}
        return {}


def test_publish_sequence_and_ai_label(creds, monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpegbytes")
    monkeypatch.setattr(media_host, "publish", lambda p: "https://cdn.example/a.jpg")
    graph = FakeGraph()
    monkeypatch.setattr(ig, "_call", graph)

    out = ig.post_image("a quiet corner", str(img), alt="a chair by a window")

    stages = [(m, p.split("/")[-1]) for m, p, _ in graph.calls]
    assert stages[:3] == [("POST", "media"), ("GET", "CONTAINER1"),
                          ("POST", "media_publish")]
    container_params = graph.calls[0][2]
    # disclosure by default, never by calculation
    assert container_params["is_ai_generated"] == "true"
    assert container_params["image_url"] == "https://cdn.example/a.jpg"
    assert container_params["alt_text"] == "a chair by a window"
    assert "image_url" in container_params and "video_url" not in container_params
    assert out["uri"] == "ig:MEDIA9" and out["url"].startswith("https://")


def test_video_publishes_as_a_reel_and_waits_for_transcode(creds, monkeypatch, tmp_path):
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"mp4bytes")
    monkeypatch.setattr(media_host, "publish", lambda p: "https://cdn.example/a.mp4")
    graph = FakeGraph(statuses=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    monkeypatch.setattr(ig, "_call", graph)

    ig.post_video("ambient corner", str(vid))

    params = graph.calls[0][2]
    assert params["media_type"] == "REELS" and params["video_url"].endswith(".mp4")
    # it must keep polling rather than publishing a half-ingested container
    polls = [c for c in graph.calls if c[1] == "CONTAINER1"]
    assert len(polls) >= 2


def test_container_error_is_raised_not_published(creds, monkeypatch, tmp_path):
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"mp4bytes")
    monkeypatch.setattr(media_host, "publish", lambda p: "https://cdn.example/a.mp4")
    graph = FakeGraph(statuses=["ERROR"])
    monkeypatch.setattr(ig, "_call", graph)

    with pytest.raises(RuntimeError, match="ERROR"):
        ig.post_video("ambient corner", str(vid))
    assert not [c for c in graph.calls if c[1].endswith("/media_publish")]


def test_caption_carries_the_persona_disclosure(creds, monkeypatch, tmp_path):
    from studio import persona

    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpegbytes")
    monkeypatch.setattr(media_host, "publish", lambda p: "https://cdn.example/a.jpg")
    graph = FakeGraph()
    monkeypatch.setattr(ig, "_call", graph)

    ig.post_image("a quiet corner", str(img), persona_id="june")
    expected = persona.load("june")["identity"]["post_disclosure"].strip()
    assert graph.calls[0][2]["caption"].endswith(expected)


def test_missing_media_host_fails_before_any_api_call(creds, monkeypatch, tmp_path):
    """Reaching Meta with an unreachable URL fails inside container polling
    with an unhelpful message — stop at the seam instead."""
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpegbytes")
    for var in ("MEDIA_HOST", "MEDIA_PUBLIC_BASE_URL", "MEDIA_LOCAL_DIR"):
        monkeypatch.delenv(var, raising=False)
    graph = FakeGraph()
    monkeypatch.setattr(ig, "_call", graph)

    with pytest.raises(RuntimeError, match="media host"):
        ig.post_image("a quiet corner", str(img))
    assert graph.calls == []


def test_local_media_host_roundtrip(monkeypatch, tmp_path):
    src = tmp_path / "render.jpg"
    src.write_bytes(b"pixels")
    served = tmp_path / "www"
    monkeypatch.setenv("MEDIA_HOST", "local")
    monkeypatch.setenv("MEDIA_LOCAL_DIR", str(served))
    monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", "https://cdn.example/media/")

    url = media_host.publish(src)
    name = url.rsplit("/", 1)[-1]
    assert (served / name).read_bytes() == b"pixels"
    assert name.endswith(".jpg")
    # content-addressed: same bytes, same URL, so republishing is idempotent
    assert media_host.publish(src) == url


def test_not_configured_is_reported_not_guessed(monkeypatch):
    for var in ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert ig.configured() is False
