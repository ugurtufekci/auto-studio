"""Public media hosting — the step Instagram cannot skip.

Every other adapter uploads bytes; Instagram takes a URL and fetches the
media itself. Generated stills already carry a provider URL, so this only
ever mattered for a LOCALLY COMPOSITED file — a carousel slide with a label
burned on, a cover frame, an assembled reel. The operator hit exactly that
on 2026-08-17: approved a six-slide carousel and got "no public media host
configured", with three infrastructure options and no easy one.
"""

import pytest

from studio import media_host


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("MEDIA_HOST", "MEDIA_PUBLIC_BASE_URL", "MEDIA_LOCAL_DIR",
                "MEDIA_S3_BUCKET", "MEDIA_S3_KEY", "MEDIA_S3_SECRET", "FAL_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_the_renderer_s_own_storage_is_the_default_and_needs_no_setup(monkeypatch):
    """The studio is already authenticated against it and already passes its
    URLs to Meta for generated stills. Making it the fallback closes the gap
    for composited files without asking the operator to run a static host or
    open an S3 account."""
    monkeypatch.setenv("FAL_KEY", "key-123")
    assert media_host.backend() == "fal"
    assert media_host.configured() is True


def test_without_a_key_and_without_a_backend_it_says_the_easy_fix_first(tmp_path):
    """The old message listed two infrastructure backends and no easy path.
    Someone reading it at the publish gate should learn the cheap answer in
    the first sentence."""
    assert media_host.backend() == ""
    assert media_host.configured() is False
    with pytest.raises(RuntimeError) as e:
        media_host.publish(tmp_path / "x.jpg")
    assert "FAL_KEY" in str(e.value)
    assert str(e.value).index("FAL_KEY") < str(e.value).index("MEDIA_HOST")


def test_an_explicit_backend_still_wins_over_the_default(monkeypatch, tmp_path):
    """A key being present must not quietly override an operator who chose
    S3 on purpose — and an incomplete S3 config stays unconfigured rather
    than silently falling back to somebody else's storage."""
    monkeypatch.setenv("FAL_KEY", "key-123")
    monkeypatch.setenv("MEDIA_HOST", "s3")
    assert media_host.backend() == "s3"
    assert media_host.configured() is False          # no bucket or credentials

    monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", "https://media.example.com/")
    monkeypatch.setenv("MEDIA_S3_BUCKET", "b")
    monkeypatch.setenv("MEDIA_S3_KEY", "k")
    monkeypatch.setenv("MEDIA_S3_SECRET", "s")
    assert media_host.configured() is True


def test_a_render_that_already_has_a_url_is_never_re_uploaded(monkeypatch, tmp_path):
    """Generated stills come back with a provider URL. Re-uploading bytes that
    are already served buys nothing and costs a round trip on every post."""
    monkeypatch.setenv("FAL_KEY", "key-123")
    called = []
    monkeypatch.setattr(media_host, "_put_fal",
                        lambda p: called.append(p) or "https://uploaded/x.jpg")
    assert media_host.publish(tmp_path / "missing.jpg",
                              "https://provider/already.jpg") == \
        "https://provider/already.jpg"
    assert called == []


def test_a_composited_file_is_uploaded_and_its_url_returned(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "key-123")
    src = tmp_path / "slide-1.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    monkeypatch.setattr(media_host, "_put_fal", lambda p: f"https://fal/{p.name}")
    assert media_host.publish(src) == "https://fal/slide-1.jpg"


def test_the_local_backend_is_unchanged(monkeypatch, tmp_path):
    """Content-addressed, so republishing the same asset is idempotent."""
    served = tmp_path / "www"
    monkeypatch.setenv("MEDIA_HOST", "local")
    monkeypatch.setenv("MEDIA_LOCAL_DIR", str(served))
    monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", "https://media.example.com/")
    src = tmp_path / "cover.jpg"
    src.write_bytes(b"some bytes")
    url = media_host.publish(src)
    assert url.startswith("https://media.example.com/")
    assert url == media_host.publish(src)            # same bytes, same URL
    assert (served / media_host.object_name(src)).read_bytes() == b"some bytes"


def test_a_carousel_child_must_not_carry_the_ai_flag(monkeypatch):
    """is_ai_generated is a TOP-LEVEL flag. A child container rejects it with
    HTTP 400, code 100 / subcode 2207100, "Invalid parameter" — and nothing
    in that message names the parameter.

    Bisected against the live API on 2026-08-18: the identical call succeeds
    the moment the key is dropped, with or without alt_text. It is why no
    carousel had ever published through the console; every one was posted by
    hand instead. The disclosure is not lost — it lives on the parent, which
    is the object the feed shows, plus the 🤖 line in the caption.
    """
    from studio import publisher_instagram as ig

    sent = []
    monkeypatch.setattr(ig, "_call",
                        lambda m, p, params: sent.append(params) or {"id": "c1"})
    monkeypatch.setattr(ig, "_user", lambda: "123")

    ig._create_container("https://x/1.jpg", "", False, "alt here",
                         carousel_item=True)
    child = sent[-1]
    assert "is_ai_generated" not in child
    assert child["is_carousel_item"] == "true"
    assert "caption" not in child          # the parent holds the caption
    assert child["alt_text"] == "alt here"

    # a single image is a top-level container and MUST still declare it
    ig._create_container("https://x/1.jpg", "a caption", False)
    single = sent[-1]
    assert single["is_ai_generated"] == "true"
    assert single["caption"] == "a caption"


def test_a_reel_container_must_not_carry_alt_text(monkeypatch):
    """A REELS container rejects alt_text: HTTP 400, code 100, "The param
    alt_text is not supported for REEL". Hit live on 2026-08-19 approving
    the villa-hall reel, so no reel had ever published through the console.

    Unlike the carousel-child case this message names the parameter, so
    there was nothing to bisect — Instagram has no alt text on a reel at
    creation time. It is set in the app afterwards, or not at all.
    """
    from studio import publisher_instagram as ig

    sent = []
    monkeypatch.setattr(ig, "_call",
                        lambda m, p, params: sent.append(params) or {"id": "c1"})
    monkeypatch.setattr(ig, "_user", lambda: "123")

    ig._create_container("https://x/1.mp4", "a caption", True, "alt here")
    reel = sent[-1]
    assert "alt_text" not in reel
    assert reel["media_type"] == "REELS"
    assert reel["video_url"] == "https://x/1.mp4"
    assert reel["caption"] == "a caption"
    assert reel["is_ai_generated"] == "true"   # top-level, still declared

    # an image container is unaffected — alt text is the accessible half of
    # a still post and must survive
    ig._create_container("https://x/1.jpg", "a caption", False, "alt here")
    assert sent[-1]["alt_text"] == "alt here"


def test_the_reel_error_explains_itself_to_the_operator():
    """The operator sees the raw Meta line. It says what is wrong but not
    that the fix is already shipped, nor that a running console keeps the
    old code after a git pull — which cost a round the last time."""
    from studio import publisher_instagram as ig

    hint = ig._hint_for_message("The param alt_text is not supported for REEL")
    assert "alt_text" in hint and "start it again" in hint


def test_a_reel_never_borrows_its_frame_still_as_the_video_url(monkeypatch, tmp_path):
    """A locally assembled reel's provenance records the FRAME it was built
    from — a PNG. Handing that PNG to Meta as video_url failed the container
    with status ERROR and no detail, deterministically: operator releases on
    2026-08-20 and 2026-08-23 both died twice in a row (the fresh-container
    retry rebuilt the same wrong URL), while the only reel that ever
    released through the console had an empty source_url. The shortcut is
    only honest when the URL is the same class of media as the file."""
    monkeypatch.setenv("FAL_KEY", "key-123")
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"video bytes")
    uploaded = []
    monkeypatch.setattr(media_host, "_put_fal",
                        lambda p: uploaded.append(p.name) or "https://fal/reel.mp4")

    out = media_host.publish(src, "https://v3b.fal.media/files/b/x/frame.png")
    assert out == "https://fal/reel.mp4" and uploaded == ["reel.mp4"]

    out = media_host.publish(src, "https://cdn/frame.png?sig=abc")
    assert out == "https://fal/reel.mp4"         # query strings hide nothing

    assert media_host.publish(src, "https://cdn/render.mp4") == "https://cdn/render.mp4"
    assert uploaded == ["reel.mp4", "reel.mp4"]  # matching class keeps the shortcut


def test_an_extensionless_provider_url_uploads_rather_than_guessing(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "key-123")
    src = tmp_path / "reel.mp4"
    src.write_bytes(b"v")
    monkeypatch.setattr(media_host, "_put_fal", lambda p: "https://fal/x.mp4")
    assert media_host.publish(src, "https://cdn/object/abc123") == "https://fal/x.mp4"
