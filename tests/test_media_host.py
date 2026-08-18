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
