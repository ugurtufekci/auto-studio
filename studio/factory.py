"""Asset factory — handbook page 08, miniature.

Over-generate and filter: N image candidates per prompt, a vision judge
picks the best (mini quality gate), TTS voiceover, ffmpeg slideshow
assembly, and the optional Wan text-to-video hero clip.

Model IDs are ordered fallback lists — the first one the account can
reach wins (provider abstraction, handbook page 21).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

MODELS = {
    "image": ["fal-ai/z-image/turbo", "fal-ai/flux/schnell"],
    "tts": ["fal-ai/kokoro/american-english", "fal-ai/kokoro"],
    "video": ["fal-ai/wan/v2.5/text-to-video", "fal-ai/wan-25-preview/text-to-video",
              "fal-ai/wan/v2.2-a14b/text-to-video"],
}


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _download(url: str, dest: Path):
    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def _run_with_fallback(kind: str, arguments: dict) -> tuple[dict, str]:
    """Try model ids in order; return (result, model_id_used)."""
    import fal_client
    last_err = None
    for model in MODELS[kind]:
        try:
            return fal_client.run(model, arguments=arguments), model
        except Exception as e:
            last_err = e
            msg = str(e)
            # balance problems won't be fixed by a different model — surface now
            if "balance" in msg.lower() or "locked" in msg.lower():
                raise
    raise RuntimeError(f"all {kind} models failed, last: {last_err}")


# ── images: over-generate → judge picks ─────────────────────────

def generate_images(prompts: list[str], run_dir: Path, per_prompt: int = 2,
                    allow_local: bool = True) -> list[dict]:
    """Each prompt rendered per_prompt times. Returns [{path, prompt, model}].

    Falls back to the local placeholder renderer when the provider is
    unreachable (out of balance, outage) so a cycle still completes and the
    failure is visible in the asset's model name rather than killing the run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for pi, prompt in enumerate(prompts):
        try:
            res, model = _run_with_fallback("image", {
                "prompt": prompt,
                "image_size": "square_hd",   # 1024² — generate at delivery size
                "num_images": per_prompt,
                "enable_safety_checker": True,
            })
        except Exception as e:
            if not allow_local:
                raise
            # Source chain: generated → licensed stock → local placeholder.
            # Each rung records its own provenance so the publish gate can
            # tell the truth about what the image actually is.
            from studio import source_pexels
            if source_pexels.configured():
                try:
                    print(f"  [factory] generation unavailable ({str(e)[:60]}) "
                          f"— falling back to Pexels stock")
                    out = []
                    for pr in prompts:
                        out.extend(source_pexels.search_photos(pr, run_dir, per_prompt))
                    if out:
                        return out
                except Exception as pe:
                    print(f"  [factory] pexels failed too ({str(pe)[:60]})")
            print("  [factory] using local placeholder renderer")
            from studio import factory_local
            return factory_local.generate_images(prompts, run_dir, per_prompt)
        for ii, img in enumerate(res["images"]):
            dest = run_dir / f"img_p{pi}_{ii}.jpg"
            _download(img["url"], dest)
            out.append({"path": str(dest), "prompt": prompt, "model": model})
    return out


def judge_pick(candidates: list[dict], brief_premise: str,
               model: str | None = None,
               persona_id: str | None = None) -> tuple[int, str]:
    """Vision judge picks the best candidate. Returns (index, reason).

    The aesthetic standard comes from the persona, not from this module: a
    judge told to look for warm coffee-shop light will pick the wrong frame
    for an interiors persona, quietly, and only the reason string reveals it.
    """
    from studio import llm
    from studio import persona as persona_cfg

    model = model or os.environ.get("JUDGE_MODEL", llm.DEFAULT_MODEL)
    vis = persona_cfg.load(persona_id).get("visual_grammar") or {}
    look = vis.get("palette", "").strip() or "the persona's established look"
    avoid = vis.get("avoid", "")
    prompt = (
        f"The images are candidates (in order: candidate 0, 1, …) for a "
        f"lifestyle post about: {brief_premise}\n"
        f"Pick the best one for an account whose visual world is: {look}. "
        "It must be photorealistic, free of garbled text, and free of "
        "anatomical or physics artifacts"
        + (f"; never pick one showing {avoid}" if avoid else "")
        + ". Otherwise choose the aesthetically strongest.\n"
        'Reply STRICT JSON only: {"pick": <index>, "reason": "<one sentence>"}')
    reply = llm.complete(prompt, model=model,
                         images=[c["path"] for c in candidates], max_tokens=200)
    verdict = llm.extract_json(reply)
    idx = int(verdict["pick"])
    if not 0 <= idx < len(candidates):
        idx = 0
    return idx, verdict.get("reason", "")


# ── TTS voiceover ───────────────────────────────────────────────

def tts(script: str, run_dir: Path, allow_local: bool = True) -> tuple[str, str]:
    """Voiceover mp3/wav for the slideshow. Returns (path, model_used)."""
    try:
        res, model = _run_with_fallback("tts", {"prompt": script, "voice": "af_heart"})
    except Exception as e:
        if not allow_local:
            raise
        print(f"  [factory] TTS provider unavailable ({str(e)[:70]}) — local fallback")
        from studio import factory_local
        return factory_local.tts(script, run_dir)
    audio_url = (res.get("audio") or {}).get("url") or res.get("audio_url")
    dest = run_dir / "voiceover.mp3"
    _download(audio_url, dest)
    return str(dest), model


# ── slideshow assembly (ffmpeg) ─────────────────────────────────

def make_slideshow(image_paths: list[str], audio_path: str | None,
                   run_dir: Path, secs_per_image: float = 3.5) -> str:
    """Stills → 1080×1080 video with slow zoom + crossfade + voiceover."""
    out_path = run_dir / "slideshow.mp4"
    n = len(image_paths)
    fps = 25
    xfade = 0.6
    # each input is a SINGLE frame; zoompan expands it to clip_len seconds
    # (looping the input first would multiply duration per frame — classic trap)
    clip_frames = int((secs_per_image + xfade) * fps)

    inputs, filters = [], []
    for i, p in enumerate(image_paths):
        inputs += ["-i", p]
        filters.append(
            f"[{i}:v]scale=1080:1080:force_original_aspect_ratio=increase,"
            f"crop=1080:1080,zoompan=z='min(zoom+0.0012,1.12)':d={clip_frames}"
            f":s=1080x1080:fps={fps},setsar=1[v{i}]")

    # chain crossfades: with clip length s+xf and fade xf, offset_k = k*s
    last = "v0"
    for i in range(1, n):
        nxt = f"x{i}"
        offset = i * secs_per_image
        filters.append(f"[{last}][v{i}]xfade=transition=fade:duration={xfade}:offset={offset:.2f}[{nxt}]")
        last = nxt

    cmd = ["ffmpeg", "-y", *inputs]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    if audio_path:
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), str(out_path)]

    subprocess.run(cmd, check=True, capture_output=True)
    return str(out_path)


# ── hero clip: true text-to-video ───────────────────────────────

def normalize_vertical(src: str, run_dir: Path, max_seconds: int = 30) -> str:
    """Crop/scale any clip to 1080×1920 and cap its length — the shape Shorts,
    Reels and Telegram all accept without re-encoding on their side."""
    out = run_dir / "hero_vertical.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", src, "-t", str(max_seconds),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               "crop=1080:1920,setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    return str(out)


def hero_clip(prompt: str, run_dir: Path, duration: int = 5,
              resolution: str = "720p") -> tuple[str, str, dict]:
    """A real moving clip. Wan text-to-video first (~$0.5-1.5); licensed Pexels
    stock as the free fallback. Returns (path, model, credit)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        res, model = _run_with_fallback("video", {
            "prompt": prompt,
            "resolution": resolution,
            "duration": duration,
        })
        video_url = (res.get("video") or {}).get("url") or res.get("video_url")
        dest = run_dir / "hero.mp4"
        _download(video_url, dest)
        return normalize_vertical(str(dest), run_dir), model, {}
    except Exception as e:
        from studio import source_pexels
        if not source_pexels.configured():
            raise
        print(f"  [factory] text-to-video unavailable ({str(e)[:60]}) "
              f"— falling back to Pexels stock video")
        path, model, credit = source_pexels.search_video(prompt, run_dir)
        return normalize_vertical(path, run_dir), model, credit


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_dir = ASSETS_DIR / f"test-{_stamp()}"
    imgs = generate_images(
        ["a ceramic flat white on a marble café counter, morning golden light, "
         "photorealistic lifestyle photography, 35mm, shallow depth of field"],
        run_dir, per_prompt=2)
    print("generated:", [i["path"] for i in imgs])
    pick, reason = judge_pick(imgs, "morning flat white ritual")
    print(f"judge picked #{pick}: {reason}")
