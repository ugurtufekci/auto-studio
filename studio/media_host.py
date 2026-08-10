"""Public media hosting — the one thing Instagram needs that nothing else does.

Every other adapter uploads bytes: Telegram takes a multipart file, Bluesky
takes a blob, YouTube takes a stream. Instagram's Graph API takes a **URL**
and fetches the media itself, so a local render has to become publicly
reachable before it can be published at all.

Two backends, chosen by MEDIA_HOST:

  local   copy into a directory that some static host already serves, and
          return PUBLIC_BASE_URL + filename. No dependencies, works with an
          rclone-synced folder, a web root, anything.
  s3      S3-compatible object storage (Cloudflare R2, S3, Backblaze B2).
          Needs boto3.

Filenames are content-addressed, so republishing the same asset is idempotent
and two personas can never collide.

.env:
  MEDIA_HOST=local|s3
  MEDIA_PUBLIC_BASE_URL=https://media.example.com/     (both backends)
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


def configured() -> bool:
    backend = os.environ.get("MEDIA_HOST", "").strip().lower()
    if not os.environ.get("MEDIA_PUBLIC_BASE_URL"):
        return False
    if backend == "local":
        return bool(os.environ.get("MEDIA_LOCAL_DIR"))
    if backend == "s3":
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


def publish(path: str | Path) -> str:
    """Make a local render publicly fetchable; return its URL.

    Raises with the missing setting named rather than half-publishing — a
    cycle that reaches Instagram with an unreachable URL fails deep inside
    Meta's container polling, where the error says nothing useful."""
    if not configured():
        raise RuntimeError(
            "no public media host configured — Instagram fetches media by URL, "
            "so set MEDIA_HOST=local|s3 plus MEDIA_PUBLIC_BASE_URL and the "
            "backend's own settings (see studio/media_host.py)")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"media to publish does not exist: {p}")
    name = object_name(p)
    backend = os.environ["MEDIA_HOST"].strip().lower()
    (_put_local if backend == "local" else _put_s3)(p, name)
    return public_url(name)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("media host not configured — see the module docstring")
    else:
        print(f"backend: {os.environ['MEDIA_HOST']} → "
              f"{os.environ['MEDIA_PUBLIC_BASE_URL']}")
