"""Asset factory — handbook page 08, miniature.

Over-generate and filter: N image candidates per prompt, a vision judge
picks the best (mini quality gate), TTS voiceover, ffmpeg slideshow
assembly, and the optional Wan text-to-video hero clip.

Model IDs are ordered fallback lists — the first one the account can
reach wins (provider abstraction, handbook page 21).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

import httpx

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    """The ffmpeg to call: the system one, else imageio-ffmpeg's static build.

    Base images routinely ship without ffmpeg, and a cloud run that got as far
    as paying for four images should not die at assembly over a missing
    binary."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # let subprocess raise with the obvious message


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
                    allow_local: bool = True, prefer: str = "generated",
                    seed: int | None = None,
                    image_size: str | dict = "square_hd",
                    tag: str = "img") -> list[dict]:
    """Each prompt rendered per_prompt times.

    `seed` renders every prompt from the same starting noise. For a
    comparison set — the same room with one decision swapped per frame — this
    is what keeps the furniture, the window and the camera identical between
    frames; without it the renderer re-invents the room each time and the
    comparison falls apart (drifting objects are exactly what gets these
    reels called out as fake).

    Returns [{path, prompt, model, url}] — `url` is the provider's own public
    URL for the render, when there is one. Instagram fetches media by URL and
    keeps its own copy, so a render that already sits at a public address does
    not need re-hosting: the URL only has to be alive for the seconds Meta
    spends ingesting it. That removes an entire storage dependency for
    generated stills.

    prefer="stock" (a persona's content.media_source) sources licensed stock
    FIRST and never touches paid generation — the operator's explicit budget
    decision for channels where stock is good enough. Its fallback is the
    local placeholder, deliberately not the paid renderer: a broken stock key
    must never quietly turn into a bill.

    Falls back to the local placeholder renderer when the provider is
    unreachable (out of balance, outage) so a cycle still completes and the
    failure is visible in the asset's model name rather than killing the run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    if prefer == "stock":
        from studio import source_pexels
        if source_pexels.configured():
            try:
                out = []
                for pr in prompts:
                    out.extend(source_pexels.search_photos(pr, run_dir, per_prompt))
                if out:
                    return out
            except Exception as pe:
                print(f"  [factory] stock-first: pexels failed ({str(pe)[:60]})")
        print("  [factory] stock-first requested but stock unavailable — using "
              "the local placeholder, never paid generation")
        from studio import factory_local
        return factory_local.generate_images(prompts, run_dir, per_prompt)
    out = []
    for pi, prompt in enumerate(prompts):
        try:
            args = {
                "prompt": prompt,
                # generate AT the delivery shape: cropping a 9:16 post out of
                # a square throws away the width a wide room lives on
                "image_size": image_size,
                "num_images": per_prompt,
                "enable_safety_checker": True,
            }
            if seed is not None:
                args["seed"] = seed
            res, model = _run_with_fallback("image", args)
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
            # `tag` keeps two renders in one run dir apart: the boards used to
            # be written as img_p0_0.jpg and silently overwrite the base room
            dest = run_dir / f"{tag}_p{pi}_{ii}.jpg"
            _download(img["url"], dest)
            out.append({"path": str(dest), "prompt": prompt, "model": model,
                        "url": img.get("url", "")})
    return out


# Instruction editors, not strength-based image-to-image. Measured on a real
# powder-room render 2026-08-16: strength 0.65 kept the source so faithfully
# that a forest-green scheme came back oxblood, and raising strength drifts
# the room instead of repainting it. Told what to change in words, these keep
# the geometry AND apply the change — flux-kontext/dev held structure best of
# the four tried (edge difference 7.5 against 12-25 for the others).
EDIT_MODELS = [("fal-ai/flux-kontext/dev", "image_url"),
               ("fal-ai/nano-banana/edit", "image_urls"),
               ("fal-ai/flux-pro/kontext", "image_url")]

KEEP_CLAUSE = ("Keep the room itself exactly as it is — same camera angle, "
               "same layout, same furniture in the same places, same window "
               "and same light. Change nothing except the materials named.")


def edit_instruction(change: str) -> str:
    """A scheme's change clause as an instruction to an editor.

    The composed t2i prompt is the wrong thing to send: it describes the whole
    room, and an editor reading it mostly re-confirms what it already sees.
    What moves the picture is the change, named, plus an explicit hold on
    everything else."""
    change = str(change or "").strip().rstrip(".")
    return f"Change the materials to: {change}. {KEEP_CLAUSE}"


def generate_variants(base: dict, changes: list[str], run_dir: Path,
                      tag: str = "room",
                      canvas: tuple[int, int] | None = None) -> list[dict]:
    """Every scheme after the first, produced by EDITING the first.

    Text-to-image cannot hold a room still: the same words and the same seed
    still re-invent the furniture, and a comparison whose furniture moves is
    what viewers call fake. Editing one render keeps the geometry — same
    basin, same mirror, same doorway — and repaints only what is named.

    Falls back to the base render for a scheme every editor refuses, so one
    failed edit costs a frame rather than the cycle."""
    import fal_client
    url = base.get("url") or fal_client.upload_file(base["path"])
    out = []
    for i, change in enumerate(changes):
        dest = run_dir / f"{tag}_v{i + 1}.jpg"
        prompt = edit_instruction(change)
        for model, url_key in EDIT_MODELS:
            try:
                args = {"prompt": prompt,
                        url_key: [url] if url_key.endswith("s") else url}
                if canvas:
                    args["image_size"] = {"width": canvas[0], "height": canvas[1]}
                res = fal_client.run(model, arguments=args)
                img = res["images"][0]
                _download(img["url"], dest)
                out.append({"path": str(dest), "prompt": change, "model": model,
                            "url": img.get("url", "")})
                break
            except Exception as e:
                print(f"  [factory] {model} failed on scheme {i + 2}: {str(e)[:70]}")
        else:
            print(f"  [factory] scheme {i + 2} kept the base render — no editor answered")
            out.append(dict(base, prompt=change))
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

    if len(candidates) <= 1:
        # a comparison set renders one shot per prompt on purpose; there is
        # nothing to judge and a vision call would only cost time
        return 0, "single render — nothing to choose between"

    model = model or os.environ.get("JUDGE_MODEL", llm.DEFAULT_MODEL)
    vis = persona_cfg.load(persona_id).get("visual_grammar") or {}
    look = vis.get("palette", "").strip() or "the persona's established look"
    avoid = str(vis.get("avoid", "")).strip()
    # A style bible names what "best" means for this account; adherence beats
    # prettiness. Without one, the palette line is the standard as before.
    criteria = str(vis.get("judge_criteria", "")).strip()
    standard = (f"Judge against this account's style contract: {criteria}"
                if criteria else
                f"Pick the best one for an account whose visual world is: {look}.")
    prompt = (
        f"The images are candidates (in order: candidate 0, 1, …) for a "
        f"lifestyle post about: {brief_premise}\n"
        f"{standard} "
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

# fonts that ship with the usual base images; first one present wins
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

# the type shrinks to fit the frame (down to 22px ≈ 77 characters), so this
# is only a backstop against a runaway line — set below what the smallest
# size can hold, never so low that it clips a spec mid-word
_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

LABEL_MAX_CHARS = 72
FRAME = 1080          # delivery width; the square is FRAME × FRAME
SQUARE = (FRAME, FRAME)
VERTICAL = (1080, 1920)   # 9:16 — the shape that fills a phone in Reels
_MARGIN, _PAD = 52, 18


def _label_anchor(canvas: tuple[int, int]) -> int:
    """How far above the bottom edge the label sits.

    In a 9:16 Reels frame the app's own furniture — caption, audio line,
    the button rail — covers the bottom of the picture, so a label pinned
    to the edge is read by nobody. It rides above that band instead."""
    w, h = canvas
    return _MARGIN if h <= w else int(h * 0.22)


def _label_font(size: int):
    """A truetype face at `size`, or None when the box has no usable font.

    Deliberately not PIL's bitmap default: it renders at a fixed tiny size,
    which on a 1080² frame is an illegible speck — worse than no label.
    """
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return None


def label_size(labels: list[str], width: int = FRAME) -> int:
    """One type size for the whole slideshow — the longest label decides it.

    Sized per frame instead, a long spec line would shrink only its own
    caption and the type would jump between cuts, which reads as a glitch
    rather than a set."""
    usable = width - 2 * _MARGIN - 2 * _PAD
    size = 44
    while size > 22:
        font = _label_font(size)
        if font is None or all(font.getbbox(x)[2] <= usable for x in labels if x):
            break
        size -= 2
    return size


def _bold_font(size: int):
    from PIL import ImageFont
    for path in _BOLD_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return _label_font(size)


def parse_spec(label: str) -> list[tuple[str, str]]:
    """"1 · walnut cabinets #5C4033 · honed marble" → the materials it names.

    Returns (material, hex) pairs. The leading frame number is dropped: it is
    an index for us, not a thing the viewer came to see."""
    out = []
    for part in [x.strip() for x in re.split(r"[·|]", label) if x.strip()]:
        if re.fullmatch(r"\d+", part):
            continue
        m = re.search(r"#[0-9A-Fa-f]{6}", part)
        name = re.sub(r"#[0-9A-Fa-f]{6}", "", part).strip(" ·—-")
        if name or m:
            out.append((name, m.group(0).upper() if m else ""))
    return out


def burn_spec_card(image_path: str, label: str, dest: Path,
                   canvas: tuple[int, int] = SQUARE) -> str:
    """The materials named BIG, inside the picture, with their colours.

    The reels that carry this format put the specification on screen at a
    size you read without trying, each material next to the colour it means.
    A small strip along the bottom edge — what this pipeline did first — is
    read as a watermark and skipped; the specification IS the content here,
    not a footnote to it."""
    from PIL import Image, ImageDraw, ImageOps

    W, H = canvas
    img = ImageOps.fit(Image.open(image_path).convert("RGB"), canvas,
                       method=Image.LANCZOS)
    rows = parse_spec(label)
    if not rows:
        img.save(dest)
        return str(dest)

    pad = int(W * 0.055)
    name_size = int(W * 0.052)
    hex_size = int(W * 0.028)
    swatch = int(W * 0.058)
    gap = int(W * 0.028)
    name_f, hex_f = _bold_font(name_size), _label_font(hex_size)
    if name_f is None:
        img.save(dest)
        return str(dest)

    line_h = max(swatch, name_size + hex_size // 2) + gap
    panel_h = line_h * len(rows) - gap + 2 * pad
    # above the app's own furniture in a 9:16 frame, low in a square one
    top = (int(H * 0.60) if H > W else H - panel_h - int(H * 0.06))
    top = min(top, H - panel_h - int(H * 0.04))

    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((pad // 2, top, W - pad // 2, top + panel_h),
                        radius=int(W * 0.028), fill=(12, 12, 12, 168))
    y = top + pad
    text_x = pad + swatch + int(W * 0.022)   # one column for every row, swatch or not
    for name, hexcode in rows:
        if hexcode:
            d.rounded_rectangle((pad, y, pad + swatch, y + swatch),
                                radius=int(swatch * 0.22),
                                fill=hexcode, outline=(255, 255, 255, 90), width=2)
        x = text_x
        d.text((x, y - 2), name.upper(), font=name_f, fill=(255, 255, 255, 245))
        if hexcode:
            d.text((x, y + name_size + 2), hexcode, font=hex_f,
                   fill=(255, 255, 255, 170))
        y += line_h
    Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB").save(dest)
    return str(dest)


def board_prompt(pairs: list[tuple[str, str]]) -> str:
    """A full-frame stack of material textures, one horizontal band each.

    The reference format shows the materials themselves full screen — the
    texture is the content — and then the room they build. Horizontal bands
    because top/middle/bottom ordering is the one spatial instruction a
    renderer follows reliably, which is what lets the names be placed on the
    right band afterwards without knowing anything else about the picture."""
    n = len(pairs)
    bands = "; ".join(f"band {i+1} — {name}, natural macro texture"
                      for i, (name, _) in enumerate(pairs))
    return (f"full-frame vertical stack of {n} interior material samples as "
            f"equal horizontal bands, edge to edge, no gaps: {bands}. "
            f"photorealistic macro material photography, soft studio light, "
            f"rich tactile detail, no text, no logos, no watermark")


def burn_band_names(image_path: str, label: str, dest: Path,
                    canvas: tuple[int, int] = SQUARE) -> str:
    """Material name + hex onto its own band of a board frame.

    Band i occupies rows [i·H/n, (i+1)·H/n) by construction of board_prompt,
    so the name lands on the texture it names without any vision call."""
    from PIL import Image, ImageDraw, ImageOps

    W, H = canvas
    img = ImageOps.fit(Image.open(image_path).convert("RGB"), canvas,
                       method=Image.LANCZOS)
    pairs = parse_spec(label)
    if not pairs:
        img.save(dest)
        return str(dest)
    name_size, hex_size = int(W * 0.056), int(W * 0.028)
    name_f, hex_f = _bold_font(name_size), _label_font(hex_size)
    if name_f is None:
        img.save(dest)
        return str(dest)
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    band_h = H / len(pairs)
    pad = int(W * 0.055)
    for i, (name, hexcode) in enumerate(pairs):
        y = int(i * band_h + band_h / 2) - name_size // 2
        text = name.upper()
        x0, y0, x1, y1 = d.textbbox((pad, y), text, font=name_f)
        # a soft dark plate, not a chip: the texture stays the subject
        d.rounded_rectangle((x0 - 18, y0 - 12, x1 + 18,
                             y1 + (hex_size + 16 if hexcode else 12)),
                            radius=12, fill=(10, 10, 10, 120))
        d.text((pad, y), text, font=name_f, fill=(255, 255, 255, 242))
        if hexcode:
            d.text((pad, y1 + 2), hexcode, font=hex_f,
                   fill=(255, 255, 255, 175))
    Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB").save(dest)
    return str(dest)


def burn_label(image_path: str, text: str, dest: Path, size: int = 44,
               canvas: tuple[int, int] = SQUARE) -> str:
    """Composite one spec line onto a still, returning the new file's path.

    The label is drawn into the PICTURE rather than added as an ffmpeg
    drawtext filter: several ffmpeg builds — including the static one that
    stands in when the box has no system ffmpeg — ship without the drawtext
    filter at all, and an assembly that dies at the last step has already
    paid for its images. Drawing here also lets the still be fitted to the
    delivery square first, so the label sits where it was measured to sit.
    """
    from PIL import Image, ImageDraw, ImageOps

    img = ImageOps.fit(Image.open(image_path).convert("RGB"),
                       canvas, method=Image.LANCZOS)
    font = _label_font(size)
    if font is None:
        img.save(dest)
        return str(dest)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    w, h = x1 - x0, y1 - y0
    left = _MARGIN
    top = canvas[1] - _label_anchor(canvas) - h - 2 * _PAD
    draw.rounded_rectangle(
        (left, top, left + w + 2 * _PAD, top + h + 2 * _PAD),
        radius=10, fill=(0, 0, 0, 140))
    draw.text((left + _PAD - x0, top + _PAD - y0), text,
              font=font, fill=(255, 255, 255, 236))
    Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB").save(dest)
    return str(dest)


def slideshow_command(frames: list[str], audio_path: str | None, run_dir: Path,
                      secs_per_image: float, labels: list[str],
                      canvas: tuple[int, int] = SQUARE,
                      cut: str = "",
                      durations: list[float] | None = None) -> list[str]:
    """The ffmpeg argv for a slideshow, split out so the graph is inspectable.

    Labels switch the cut into COMPARISON mode: the camera stops drifting and
    the dissolve shortens, because a comparison only reads if the frame holds
    still and the swap lands as a change rather than a blur."""
    out_path = run_dir / "slideshow.mp4"
    n = len(frames)
    w, h = canvas
    fps = 25
    comparison = any(labels)
    # "hard" is a cut, not a dissolve: the reference reels change the room
    # between one frame and the next with nothing in between, and the change
    # landing instantly IS the effect. 0.08s rather than 0 because xfade needs
    # a positive duration — at 25fps that is two frames, invisible as a fade.
    xfade = 0.08 if cut == "hard" else (0.25 if comparison else 0.6)
    zoom = "1" if (comparison or cut == "hard") else "min(zoom+0.0012,1.12)"
    durs = list(durations or []) or [secs_per_image] * n
    durs += [secs_per_image] * (n - len(durs))

    inputs, filters = [], []
    for i, p in enumerate(frames):
        inputs += ["-i", p]
        # each input is a SINGLE frame; zoompan expands it to its clip length
        # (looping the input first would multiply duration per frame)
        clip_frames = int((durs[i] + xfade) * fps)
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},zoompan=z='{zoom}':d={clip_frames}"
            f":s={w}x{h}:fps={fps},setsar=1[v{i}]")

    # chain crossfades: with per-clip lengths, offset_k = sum(durs[:k])
    last = "v0"
    for i in range(1, n):
        nxt = f"x{i}"
        offset = sum(durs[:i])
        filters.append(f"[{last}][v{i}]xfade=transition=fade:duration={xfade}:offset={offset:.2f}[{nxt}]")
        last = nxt

    cmd = [ffmpeg_bin(), "-y", *inputs]
    if audio_path:
        cmd += ["-i", audio_path]
    else:
        # A SILENT TRACK, not the absence of one. A file with no audio stream
        # at all is not a normal video to Instagram: the upload flow can hide
        # its audio tools entirely, and the operator — whose whole job here is
        # to drop a trending track on top — never gets the option. A stereo
        # silence stream costs a few KB and makes the file ordinary.
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]",
            "-map", f"{n}:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart", str(out_path)]
    return cmd


def make_slideshow(image_paths: list[str], audio_path: str | None,
                   run_dir: Path, secs_per_image: float = 3.5,
                   labels: list[str] | None = None,
                   canvas: tuple[int, int] = SQUARE,
                   label_style: str = "chip", cut: str = "",
                   durations: list[float] | None = None) -> str:
    """Stills → 1080×1080 video with slow zoom + crossfade + voiceover.

    `labels` puts one spec line on each frame (material + hex colour) and
    turns the slideshow into a comparison — see slideshow_command."""
    run_dir.mkdir(parents=True, exist_ok=True)
    n = len(image_paths)
    labels = [str(x or "")[:LABEL_MAX_CHARS].rstrip() for x in (labels or [])]
    labels += [""] * (n - len(labels))

    frames = list(image_paths)
    size = label_size(labels, canvas[0])
    for i, text in enumerate(labels):
        if not text or label_style == "none":
            continue
        try:
            dest = run_dir / f"frame-{i}.png"
            frames[i] = (burn_spec_card(frames[i], text, dest, canvas)
                         if label_style == "card"
                         else burn_label(frames[i], text, dest, size, canvas))
        except Exception as e:      # a missing font or PIL is not worth the run
            print(f"  [factory] label {i} not drawn ({str(e)[:60]})")

    cmd = slideshow_command(frames, audio_path, run_dir, secs_per_image, labels,
                            canvas, cut, durations)
    subprocess.run(cmd, check=True, capture_output=True)
    return str(run_dir / "slideshow.mp4")


# ── hero clip: true text-to-video ───────────────────────────────

def normalize_vertical(src: str, run_dir: Path, max_seconds: int = 30) -> str:
    """Crop/scale any clip to 1080×1920 and cap its length — the shape Shorts,
    Reels and Telegram all accept without re-encoding on their side."""
    out = run_dir / "hero_vertical.mp4"
    subprocess.run([
        ffmpeg_bin(), "-y", "-i", src, "-t", str(max_seconds),
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
