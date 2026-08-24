"""Public media hosting — the one thing Instagram needs that nothing else does.

Every other adapter uploads bytes: Telegram takes a multipart file, Bluesky
takes a blob, YouTube takes a stream. Instagram's Graph API takes a **URL**
and fetches the media itself, so a local render has to become publicly
reachable before it can be published at all.

Three backends, chosen by MEDIA_HOST:

  fal     the renderer's own file storage, which the studio is already using
          and already authenticated against. NO SETUP AT ALL — it is the
          default when nothing else is configured. See the note below.
  local   copy into a directory that some static host already serves, and
          return PUBLIC_BASE_URL + filename. No dependencies, works with an
          rclone-synced folder, a web root, anything.
  s3      S3-compatible object storage (Cloudflare R2, S3, Backblaze B2).
          Needs boto3.

Why fal is the default, and why that is not a new dependency: every
generated still already comes back with a fal URL, and the Instagram adapter
already hands those straight to Meta rather than re-hosting them — the
consumer only needs the media reachable for the seconds it spends fetching.
The gap this closes is the LOCALLY COMPOSITED file: a carousel slide with a
label burned on, a cover frame, an assembled reel. Those are new bytes with
no address, and before this they hit "no public media host configured" at
the publish gate with three infrastructure options and no easy one.

Filenames are content-addressed, so republishing the same asset is idempotent
and two personas can never collide. (fal names its own objects; the rest is
unchanged.)

.env:
  MEDIA_HOST=fal|local|s3                              (default: fal)
  MEDIA_PUBLIC_BASE_URL=https://media.example.com/     (local and s3)
  MEDIA_LOCAL_DIR=/srv/www/media                       (local)
  MEDIA_S3_BUCKET=studio-media                         (s3)
  MEDIA_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com   (s3, optional)
  MEDIA_S3_KEY / MEDIA_S3_SECRET                       (s3)
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".mp4": "video/mp4", ".mov": "video/quicktime"}


def backend() -> str:
    """Which backend to use. Unset means fal, when there is a key for it."""
    chosen = os.environ.get("MEDIA_HOST", "").strip().lower()
    if chosen:
        return chosen
    return "fal" if os.environ.get("FAL_KEY") else ""


def configured() -> bool:
    which = backend()
    if which == "fal":
        return bool(os.environ.get("FAL_KEY"))
    if not os.environ.get("MEDIA_PUBLIC_BASE_URL"):
        return False
    if which == "local":
        return bool(os.environ.get("MEDIA_LOCAL_DIR"))
    if which == "s3":
        return all(os.environ.get(k) for k in
                   ("MEDIA_S3_BUCKET", "MEDIA_S3_KEY", "MEDIA_S3_SECRET"))
    return False


def object_name(path: str | Path) -> str:
    """Content-addressed name: same bytes → same URL, always."""
    p = Path(path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return f"{digest}{p.suffix.lower()}"


def public_url(name: str) -> str:
    return os.environ["MEDIA_PUBLIC_BASE_URL"].rstrip("/") + "/" + name


def _put_local(path: Path, name: str) -> None:
    dest_dir = Path(os.environ["MEDIA_LOCAL_DIR"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest_dir / name)


def _put_fal(path: Path) -> str:
    """Upload to the renderer's own storage and return the public URL.

    Retried, because losing an approved post to one socket timeout means the
    operator presses Approve again and wonders whether it double-posted."""
    from studio import factory

    return factory.upload(str(path))


def _put_s3(path: Path, name: str) -> None:
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError(
            "MEDIA_HOST=s3 needs boto3 — `pip install boto3`, or use "
            "MEDIA_HOST=local with a directory a static host already serves") from e
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("MEDIA_S3_ENDPOINT") or None,
        aws_access_key_id=os.environ["MEDIA_S3_KEY"],
        aws_secret_access_key=os.environ["MEDIA_S3_SECRET"],
        region_name=os.environ.get("MEDIA_S3_REGION", "auto"),
    )
    client.upload_file(
        str(path), os.environ["MEDIA_S3_BUCKET"], name,
        ExtraArgs={"ContentType": MIME.get(path.suffix.lower(),
                                           "application/octet-stream")})


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _media_class(name: str | Path) -> str:
    from urllib.parse import urlparse
    suffix = Path(urlparse(str(name)).path).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return ""


def publish(path: str | Path, known_url: str = "") -> str:
    """Make a local render publicly fetchable; return its URL.

    A render that already has a public address (a provider URL from the asset
    factory) is returned as-is — but ONLY when that URL is the same class of
    media as the file being published. A reel is assembled locally, and its
    provenance records the representative FRAME it was built from: a PNG.
    Handing that PNG to Meta as `video_url` fails the container with status
    ERROR and no detail — deterministically, which read as a transient
    platform hiccup and survived a fresh-container retry (operator releases
    on 2026-08-20 and -23; a probe that uploaded the actual file succeeded,
    which is how the wrong URL, not the media, was finally cornered). On a
    class mismatch the real file is uploaded instead.

    Raises with the missing setting named rather than half-publishing — a
    cycle that reaches Instagram with an unreachable URL fails deep inside
    Meta's container polling, where the error says nothing useful."""
    if known_url and _media_class(known_url) == _media_class(path) != "":
        return known_url
    if not configured():
        raise RuntimeError(
            "no public media host configured — Instagram fetches media by URL. "
            "The easiest fix is nothing at all: with FAL_KEY set, media goes to "
            "the renderer's own storage automatically. Otherwise set "
            "MEDIA_HOST=local|s3 plus MEDIA_PUBLIC_BASE_URL and that backend's "
            "settings (see studio/media_host.py)")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"media to publish does not exist: {p}")
    which = backend()
    if which == "fal":
        return _put_fal(p)
    name = object_name(p)
    (_put_local if which == "local" else _put_s3)(p, name)
    return public_url(name)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("media host not configured — see the module docstring")
    else:
        print(f"backend: {os.environ['MEDIA_HOST']} → "
              f"{os.environ['MEDIA_PUBLIC_BASE_URL']}")
