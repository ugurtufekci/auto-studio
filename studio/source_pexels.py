"""Pexels stock source — free, licensed, real photography.

Sits between fal.ai generation and the local placeholder in the factory's
fallback chain. Assets from here are recorded with the photographer's name so
the publish gate can credit them correctly: a Pexels photo is NEVER presented
as AI-generated (see studio/publisher.compose_plain / provenance disclosure).

.env:
  PEXELS_API_KEY=...
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

API = "https://api.pexels.com"


def configured() -> bool:
    return bool(os.environ.get("PEXELS_API_KEY"))


def _headers() -> dict:
    return {"Authorization": os.environ["PEXELS_API_KEY"]}


def _download(url: str, dest: Path):
    r = httpx.get(url, timeout=180, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def _query_from_prompt(prompt: str) -> str:
    """Image prompts are long and stylistic; Pexels wants a few plain nouns."""
    head = prompt.split(",")[0].lower()
    stop = {"a", "an", "the", "of", "on", "in", "with", "and", "at", "close",
            "up", "shot", "from", "above", "photorealistic", "lifestyle",
            "photography", "natural", "light", "warm", "tones", "no", "text",
            "logos", "people's", "faces", "shallow", "depth", "field", "35mm"}
    words = [w.strip(".:;'\"") for w in head.split()]
    keep = [w for w in words if w and w not in stop][:5]
    return " ".join(keep) or "coffee cafe"


def search_photos(prompt: str, run_dir: Path, count: int = 2,
                  orientation: str = "square") -> list[dict]:
    """Topical photos for one prompt. Returns factory-shaped asset dicts."""
    run_dir.mkdir(parents=True, exist_ok=True)
    query = _query_from_prompt(prompt)
    r = httpx.get(f"{API}/v1/search", headers=_headers(), timeout=60,
                  params={"query": query, "per_page": max(count * 2, 6),
                          "orientation": orientation, "size": "large"})
    if r.status_code != 200:
        raise RuntimeError(f"pexels search failed: HTTP {r.status_code} {r.text[:120]}")
    photos = r.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"pexels returned no photos for '{query}'")
    out = []
    for i, p in enumerate(photos[:count]):
        src = p["src"].get("large2x") or p["src"].get("large") or p["src"]["original"]
        dest = run_dir / f"pexels_{abs(hash(prompt)) % 9999}_{i}.jpg"
        _download(src, dest)
        out.append({
            "path": str(dest),
            "prompt": prompt,
            "model": f"pexels:{p.get('photographer', 'unknown')}",
            "credit": {"photographer": p.get("photographer", ""),
                       "photographer_url": p.get("photographer_url", ""),
                       "source_url": p.get("url", ""), "id": p.get("id"),
                       "query": query},
        })
    return out


def search_video(prompt: str, run_dir: Path, min_seconds: int = 3,
                 max_seconds: int = 30) -> tuple[str, str, dict]:
    """One topical stock clip. Returns (path, model, credit)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    query = _query_from_prompt(prompt)
    r = httpx.get(f"{API}/videos/search", headers=_headers(), timeout=60,
                  params={"query": query, "per_page": 10, "orientation": "portrait"})
    if r.status_code != 200:
        raise RuntimeError(f"pexels video search failed: HTTP {r.status_code}")
    vids = [v for v in r.json().get("videos", [])
            if min_seconds <= (v.get("duration") or 0) <= max_seconds]
    if not vids:
        raise RuntimeError(f"pexels returned no usable video for '{query}'")
    v = vids[0]
    # pick the largest file under ~1080p so uploads stay small
    files = sorted((f for f in v["video_files"] if (f.get("height") or 0) <= 1080),
                   key=lambda f: -(f.get("height") or 0))
    if not files:
        files = v["video_files"]
    dest = run_dir / "pexels_clip.mp4"
    _download(files[0]["link"], dest)
    credit = {"photographer": v.get("user", {}).get("name", ""),
              "photographer_url": v.get("user", {}).get("url", ""),
              "source_url": v.get("url", ""), "id": v.get("id"), "query": query}
    return str(dest), f"pexels:{credit['photographer']}", credit


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("not configured — set PEXELS_API_KEY in .env")
    else:
        d = Path(__file__).resolve().parent.parent / "assets" / "pexels-smoke"
        got = search_photos("a ceramic flat white on a marble café counter, "
                            "morning golden light", d, count=2)
        for g in got:
            print(f"  {g['path']} · {g['model']} · {g['credit']['source_url']}")
