"""Local render fallback — no external provider, no cost.

Purpose: prove the pipeline end to end (and keep cycles running) when the
media provider is unavailable. Images are honest placeholders in the persona's
palette carrying the prompt text; voiceover uses the OS TTS. When fal.ai is
funded, factory.py takes over and nothing else in the pipeline changes.

Assets produced here are marked model="local-placeholder" so the dashboard
and the lineage store never confuse them with real generated media.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

PALETTE = [  # Mara's warm-neutral world, seeded per prompt for variety
    ((28, 24, 22), (122, 86, 58)),
    ((22, 26, 24), (86, 110, 74)),
    ((30, 26, 30), (140, 104, 76)),
    ((24, 22, 26), (96, 88, 120)),
]


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def generate_images(prompts: list[str], run_dir: Path,
                    per_prompt: int = 2) -> list[dict]:
    """Gradient cards in the persona palette, prompt text rendered on them."""
    from PIL import Image, ImageDraw, ImageFont

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Futura.ttc", 34)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Futura.ttc", 20)
    except Exception:
        font = small = ImageFont.load_default()

    out = []
    for pi, prompt in enumerate(prompts):
        for ii in range(per_prompt):
            seed = int(hashlib.md5(f"{prompt}{ii}".encode()).hexdigest()[:8], 16)
            top, bottom = PALETTE[seed % len(PALETTE)]
            if ii % 2:
                top, bottom = bottom, top
            img = Image.new("RGB", (1024, 1024), top)
            d = ImageDraw.Draw(img)
            for y in range(1024):   # vertical gradient
                t = y / 1023
                d.line([(0, y), (1024, y)], fill=(
                    int(top[0] + (bottom[0] - top[0]) * t),
                    int(top[1] + (bottom[1] - top[1]) * t),
                    int(top[2] + (bottom[2] - top[2]) * t)))
            # soft vignette band for text legibility
            d.rectangle([70, 700, 954, 954], fill=(0, 0, 0))
            lines = _wrap(d, prompt.split(",")[0][:150], font, 820)[:4]
            y = 730
            for ln in lines:
                d.text((100, y), ln, font=font, fill=(236, 235, 230))
                y += 44
            d.text((100, 900), f"LOCAL PLACEHOLDER · candidate {ii} · "
                               f"prompt {pi}", font=small, fill=(156, 154, 146))
            dest = run_dir / f"img_p{pi}_{ii}.jpg"
            img.save(dest, "JPEG", quality=88)
            out.append({"path": str(dest), "prompt": prompt,
                        "model": "local-placeholder"})
    return out


def tts(script: str, run_dir: Path) -> tuple[str, str]:
    """macOS `say` → AIFF → ffmpeg → mp3. No API, no cost."""
    run_dir.mkdir(parents=True, exist_ok=True)
    aiff = run_dir / "voiceover.aiff"
    mp3 = run_dir / "voiceover.mp3"
    subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), script],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(aiff), "-b:a", "128k", str(mp3)],
                   check=True, capture_output=True)
    aiff.unlink(missing_ok=True)
    return str(mp3), "local-say"


if __name__ == "__main__":
    d = Path(__file__).resolve().parent.parent / "assets" / "local-smoke"
    imgs = generate_images(["a ceramic flat white on a marble counter, morning light"],
                           d, per_prompt=2)
    print("images:", [i["path"] for i in imgs])
    path, model = tts("cold brew season, slow mornings in the city.", d)
    print("audio:", path, model)
