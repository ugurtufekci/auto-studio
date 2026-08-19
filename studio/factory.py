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


SILENT_DBFS = -60.0        # below this there is nothing a viewer would hear
_AUDIBLE_CACHE: dict[tuple[str, float], bool] = {}


def has_audible_sound(path: str) -> bool | None:
    """Whether this file would actually be heard. None when it cannot be told.

    Measured, not assumed from the container. Every reel this pipeline makes
    carries an AAC track: a style-morph reel has the voice naming each style
    over a music bed, and a material-board reel has an equally valid track of
    pure silence, because that format expects a trending song added in the
    app. Both report "Audio: aac, stereo" — only the level tells them apart
    (-38 dBFS against -91 on 2026-08-19), and getting this wrong is what led
    to telling the operator a post needed re-doing when it did not.

    Reads the first seconds only: silence here is silence throughout, since
    the track is built in one pass."""
    import os
    import subprocess

    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _AUDIBLE_CACHE:
        return _AUDIBLE_CACHE[key]
    try:
        r = subprocess.run([ffmpeg_bin(), "-v", "info", "-t", "6", "-i", path,
                            "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    peak = None
    for line in r.stderr.splitlines():
        if "max_volume:" in line:
            try:
                peak = float(line.split("max_volume:")[1].split("dB")[0])
            except ValueError:
                pass
    if peak is None:
        return None
    _AUDIBLE_CACHE[key] = peak > SILENT_DBFS
    return _AUDIBLE_CACHE[key]


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _download(url: str, dest: Path):
    r = httpx.get(url, timeout=120, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def upload(path: str, attempts: int = 3) -> str:
    """fal's uploader, retried with a backoff.

    A run that has already paid for six renders should not be thrown away by
    one socket timeout on the way back up — which is exactly what happened
    on 2026-08-17, six seconds into assembly, with the message "timed out"
    and nothing else to show for the spend."""
    import fal_client

    last = None
    for i in range(attempts):
        try:
            return fal_client.upload_file(str(path))
        except Exception as e:
            last = e
            if i + 1 < attempts:
                print(f"  [factory] upload of {Path(path).name} failed "
                      f"({str(e)[:50] or type(e).__name__}) — retrying")
                time.sleep(2 ** i)
    raise RuntimeError(f"upload failed after {attempts} attempts: {last}")


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


# The morph format's whole claim is that one room is re-dressed in front of
# you. That only survives if the keyframes differ in their SURFACES and in
# nothing else: given two rooms with different furniture in different places,
# a video model has nothing to interpolate and can only cross-fade — which
# is what made the first reels read as consecutive photographs. The
# operator's words for what this should look like: "sanki üstünü çıkarır
# gibi", as if the room's covering were pulled off.
RESKIN_CLAUSE = (
    "DO NOT move, remove, add or reshape a single object. Every piece of "
    "furniture stays exactly where it is, at exactly the same size and with "
    "exactly the same silhouette — the same sofa, the same chairs, the same "
    "table, the same shelving, the same lamp, in the same places. Only what "
    "they are MADE OF changes: the wall surface, the floor covering, the "
    "upholstery fabric, the woodwork, the metalwork, the shades, the "
    "curtains and the rug's pattern. Same camera, same framing, same window, "
    "same light direction and the same shadows. Tidy away loose clutter, "
    "papers and boxes; leave every piece of furniture standing.")


def edit_instruction(change: str, blunt: bool = False,
                     mode: str = "materials") -> str:
    """A scheme's change clause as an instruction to an editor.

    The composed t2i prompt is the wrong thing to send: it describes the whole
    room, and an editor reading it mostly re-confirms what it already sees.
    What moves the picture is the change, named, plus an explicit hold on
    everything else."""
    change = str(change or "").strip().rstrip(".")
    if mode == "reskin":
        emphasis = ("EVERY visible surface must clearly become the stated "
                    "material. " if blunt else "")
        return (f"Re-skin every surface of this room in these materials: "
                f"{change}. {emphasis}{RESKIN_CLAUSE}")
    if blunt:
        # Shorter and imperative, for a second attempt. The polite phrasing
        # can be satisfied by changing almost nothing, and a room that kept
        # its old walls is the failure a viewer sees first — the board says
        # forest green while the room is still oxblood.
        return (f"Apply these finishes: {change}. EACH SURFACE TAKES ITS OWN "
                f"MATERIAL — the walls, the cabinets, the worktop and the "
                f"floor are different things and must stay clearly different "
                f"from each other. Never paint the whole room one colour. "
                f"Keep the camera, the layout and the furniture positions.")
    # WALLS FIRST, because the walls are what keeps not changing. Measured
    # on the run of 2026-08-18: four schemes of a library nook differed only
    # in their FLOOR — the oxblood walls, the shelving and the banquette
    # came back identical in all four, while every label promised them all
    # swapping. The largest surface is the one an editor skips.
    return (f"START WITH THE WALLS — repaint or resurface every wall, and do "
            f"not leave them as they are. Then: {change}. Each surface takes "
            f"its own material and they stay clearly different from each "
            f"other — never one colour over the whole room. {KEEP_CLAUSE}")


# How different two schemes must look.
#
# This is deliberately NOT a comparison against the stated hex. A painted
# wall photographed under warm light never equals its own paint chip: the
# ink-blue room measured 178 away from the ink-blue it actually was. What
# the format promises is not "this wall is exactly #2F2F2F", it is "these
# are five different rooms" — so that is what is checked.
#
# Recalibrated 2026-08-19, against the frames rather than from memory. Eight
# pairs from three runs, each looked at before it was labelled:
#
#   pair                                truth       p85
#   114050 base→sage cabinets           changed      15
#   114050 base→terracotta floor        changed      44
#   114050 base→orange room             changed      83
#   115232 base→v1, edit did nothing    SAME          3
#   115232 base→terracotta floor        changed      44
#   115232 base→dark herringbone        changed      73
#   103023 base→v1                      changed      77
#   103023 base→v3                      changed      69
#
# One true negative is a thin sample, so the threshold sits with margin on
# both sides rather than close to either: 2.7× above the frame that did not
# change, 1.9× below the weakest real change. Widen it only against more
# frames, and add them to this table when you do.
SCHEME_MIN_DISTANCE = 8


CHANGED_QUANTILE = 0.85    # "the part of the frame that changed most"


def frame_distance(a: str, b: str) -> float:
    """How different two renders look. 0 is identical.

    The 85th percentile of the per-pixel difference, not the mean. A mean
    answers "how much did the average pixel move", which is the wrong
    question once the rooms are wide: a lake, a mountain and a ceiling that
    cannot change fill half the frame and dilute every real repaint toward
    zero. Repainting the cabinets of a big kitchen measured 7.8 by the mean
    against a floor of 18 and was thrown away (run 20260819-114050). The
    question the gate actually asks is whether SOME MEANINGFUL PART of the
    room changed, and a high percentile asks exactly that."""
    from PIL import Image, ImageChops

    ims = [Image.open(p).convert("RGB").resize((64, 64)) for p in (a, b)]
    diff = sorted(ImageChops.difference(*ims).convert("L").getdata())
    return float(diff[min(int(len(diff) * CHANGED_QUANTILE), len(diff) - 1)])


# A room whose walls, cabinets and floor all came back one colour. The
# operator's words for it: "mutfak ya komple yeşil ya komple turuncu gibi
# tek renkten ibaret olmamalı yani kötü duruyor".
#
# Two measurements, both taken on the four real kitchen frames of
# 2026-08-18 rather than guessed. The label is the yardstick: it already
# names three or four materials with their hex codes, so the render can be
# asked to show the range it promised.
#
#   frame                     promised  delivered  ratio   saturation
#   1 warm neutral   good        0.43      0.22     0.52      0.19
#   2 forest green   FLOODED     0.83      0.07     0.09      0.21
#   3 terracotta     FLOODED     0.14      0.05     0.39      0.64
#   4 white/grey     good        0.17      0.37     2.15      0.01
#
# Ratio catches the second: a label promising near-black cabinets under a
# white worktop, delivered as one flat green. Saturation catches the third,
# where the whole room screams one orange and the label's own range was
# narrow enough for the ratio to nearly pass it.
FLAT_RATIO = 0.40          # of the lightness range its own label promised
# Measured over eight real schemes on 2026-08-19 (runs 103023 and 114050):
# the two rooms the operator called flooded scored 0.54 and 0.59 mean
# saturation with 9° and 13° of hue between their surfaces; the six good
# ones scored 0.15–0.28. The margin is wide, so the threshold sits in it.
FLOOD_SATURATION = 0.45    # one hue, this saturated, over the whole frame
FLOOD_HUE_SPREAD = 20      # degrees between the surfaces that carry colour
FLOOD_HUE_MIN_SAT = 0.15   # a near-grey surface has no hue worth comparing
FLOOD_LUMA_FLOOR = 0.12    # below this HLS reports hue on what is just black
FLOOD_LUMA_CEILING = 0.88  # above it, on a window that is just white


MATERIAL_COLOURS = 8      # a room is read as this many material clusters
MATERIAL_MIN_SHARE = 0.06  # below this a cluster is a reflection, not a surface


def _surfaces(image_path: str) -> list[tuple[float, float, float, float]]:
    """The room's real materials: (share, hue°, lightness, saturation) each.

    This used to average three horizontal bands, which reads a room by
    height — ceiling, working level, floor. It failed the moment the rooms
    got good: one band across a wide kitchen holds pale wall, dark cabinets,
    a lit worktop and a window onto a lake, and their MEAN is a middling
    grey. So the richer the room, the flatter it measured, and the gate
    dropped three of four schemes that were plainly different from each
    other (run 20260819-114050).

    Quantising instead asks what surfaces are actually present and how much
    of the frame each one covers, which is the question — where they sit
    does not matter."""
    from PIL import Image

    im = Image.open(image_path).convert("RGB").resize((200, 250))
    q = im.quantize(colors=MATERIAL_COLOURS, method=Image.MEDIANCUT)
    palette, total, out = q.getpalette(), 200 * 250, []
    for count, idx in q.getcolors() or []:
        share = count / total
        if share < MATERIAL_MIN_SHARE:
            continue
        h, l, s = _hls(palette[idx * 3:idx * 3 + 3])
        out.append((share, h * 360, l, s))
    return out or [(1.0, 0.0, 0.5, 0.0)]


def _hls(rgb):
    import colorsys

    r, g, b = (c / 255 for c in rgb)
    return colorsys.rgb_to_hls(r, g, b)


def monochrome_flood(image_path: str, label: str) -> str:
    """"" when the room shows the materials its label names, else why not.

    Free, and it runs before anything is published — the images are already
    paid for, so the only question is whether they go out."""
    codes = [h for _, h in parse_spec(label) if h]
    surfaces = _surfaces(image_path)
    lums = [l for _, _, l, _ in surfaces]
    delivered = max(lums) - min(lums)

    if len(codes) >= 2:
        wanted = [_hls(_rgb(c))[1] for c in codes]
        promised = max(wanted) - min(wanted)
        if promised > 0.05 and delivered / promised < FLAT_RATIO:
            return (f"the label promises {len(codes)} materials spanning "
                    f"{promised:.2f} in lightness and the room shows "
                    f"{delivered:.2f} — the surfaces came back as one flat "
                    f"colour instead of walls, cabinets and floor")

    # A flood is not "saturated" — a room may be richly coloured and right.
    # It is saturated AND all one hue: every surface the same shade of
    # terracotta, which is what the operator kept rejecting.
    #
    # Judged only on the surfaces that carry a real colour. HLS puts a hue
    # and a saturation on everything, including white: a blown-out window at
    # (252,250,246) reports 0.50 saturation at 40°, which is noise, and
    # enough of it to drag a flooded room back under the threshold. Near-
    # white and near-black are excluded for that reason, not for tidiness.
    coloured = [(share, h, s) for share, h, l, s in surfaces
                if FLOOD_LUMA_FLOOR < l < FLOOD_LUMA_CEILING]
    if not coloured:
        return ""
    sat = (sum(share * s for share, _, s in coloured)
           / sum(share for share, _, _ in coloured))
    hues = [h for _, h, s in coloured if s > FLOOD_HUE_MIN_SAT]
    spread = max((min(abs(a - b) % 360, 360 - abs(a - b) % 360)
                  for a in hues for b in hues), default=0.0)
    if sat > FLOOD_SATURATION and spread < FLOOD_HUE_SPREAD:
        return (f"every surface is the same saturated colour "
                f"(saturation {sat:.2f}, hues within {spread:.0f}°) — the "
                f"whole room was painted one shade")
    return ""


def scheme_mismatch(path: str, accepted: list[str], label: str,
                    number: int) -> tuple[str, float | None]:
    """The two gates a scheme has to pass, and how far it landed from its
    nearest neighbour. "" means it passed.

    Both gates cost nothing and run after the image is paid for, so the only
    question either answers is whether it goes out. Extracted so that frames
    reused from an earlier run are judged by exactly the rule that judged
    them the first time — when the gate itself is what was wrong, re-judging
    the same pictures has to be possible without re-buying them."""
    flooded = monochrome_flood(path, label)
    if flooded:
        return f"scheme {number}: {flooded}", None
    twin = min(((frame_distance(path, p), n) for n, p in enumerate(accepted)),
               default=(999.0, 0))
    if twin[0] >= SCHEME_MIN_DISTANCE:
        return "", round(twin[0], 1)
    same = "the base room" if twin[1] == 0 else f"scheme {twin[1] + 1}"
    # one decimal, because a 17.6 printed as "18" next to "18 is the floor"
    # reads as a bug in the gate rather than a failed edit
    return (f"scheme {number} still looks like {same} ({twin[0]:.1f} apart, "
            f"{SCHEME_MIN_DISTANCE} is the floor) — its edit did not take",
            round(twin[0], 1))


def rejudge(base_path: str, paths: list[str],
            labels: list[str] | None = None) -> list[dict]:
    """Frames already on disk, put back through the scheme gates.

    Same order and same cumulative comparison as a fresh render: each scheme
    is measured against the base and every scheme accepted before it."""
    labels = list(labels or [])
    accepted, out = [base_path], []
    for i, path in enumerate(paths):
        note, near = scheme_mismatch(path, accepted,
                                     labels[i] if i < len(labels) else "",
                                     i + 2)
        got = {"path": path, "model": "reused", "url": ""}
        if note:
            got["mismatch"] = note
        if near is not None:
            got["nearest"] = near
        accepted.append(path)
        out.append(got)
    return out


def generate_variants(base: dict, changes: list[str], run_dir: Path,
                      tag: str = "room",
                      canvas: tuple[int, int] | None = None,
                      mode: str = "materials",
                      labels: list[str] | None = None) -> list[dict]:
    """Every scheme after the first, produced by EDITING the first.

    Text-to-image cannot hold a room still: the same words and the same seed
    still re-invent the furniture, and a comparison whose furniture moves is
    what viewers call fake. Editing one render keeps the geometry — same
    basin, same mirror, same doorway — and repaints only what is named.

    Falls back to the base render for a scheme every editor refuses, so one
    failed edit costs a frame rather than the cycle."""
    import fal_client
    url = base.get("url") or upload(base["path"])
    labels = list(labels or [])
    out = []
    # every scheme is compared with the ones already accepted, base included
    accepted = [base["path"]]
    for i, change in enumerate(changes):
        dest = run_dir / f"{tag}_v{i + 1}.jpg"
        got = None
        for attempt, prompt in enumerate(
                (edit_instruction(change, mode=mode),
                 edit_instruction(change, blunt=True, mode=mode))):
            for model, url_key in EDIT_MODELS:
                try:
                    args = {"prompt": prompt,
                            url_key: [url] if url_key.endswith("s") else url}
                    if canvas:
                        args["image_size"] = {"width": canvas[0], "height": canvas[1]}
                    res = fal_client.run(model, arguments=args)
                    img = res["images"][0]
                    _download(img["url"], dest)
                    got = {"path": str(dest), "prompt": change, "model": model,
                           "url": img.get("url", "")}
                    break
                except Exception as e:
                    print(f"  [factory] {model} failed on scheme {i + 2}: {str(e)[:70]}")
            if not got:
                break
            # A scheme can be different enough from its neighbours and still
            # be wrong: the whole room painted one colour is very different
            # from the room before it. Both gates, in the order they cost
            # nothing.
            note, near = scheme_mismatch(
                got["path"], accepted, labels[i] if i < len(labels) else "",
                i + 2)
            if near is not None:
                got["nearest"] = near
            if not note:
                break
            if attempt == 0:
                print(f"  [factory] {note}. Asking again, bluntly")
                continue
            got["mismatch"] = note
            print(f"  [factory] {got['mismatch']}")
        if not got:
            print(f"  [factory] scheme {i + 2} kept the base render — no editor answered")
            got = dict(base, prompt=change)
        accepted.append(got["path"])
        out.append(got)
    return out


def chain_variants(base: dict, stages: list[str], run_dir: Path,
                   tag: str = "stage",
                   canvas: tuple[int, int] | None = None) -> list[dict]:
    """Each stage edited from the PREVIOUS one, not from the base.

    generate_variants fans out — every scheme is an alternative reading of the
    same room, so each is edited from the original. A build sequence is the
    opposite shape: the floor is still there when the joinery goes in, and the
    joinery is still there when the furniture arrives. Editing every stage
    from the base would show six unrelated half-finished rooms instead of one
    room being finished.

    No distance gate here for the same reason: consecutive stages of a real
    fit-out SHOULD look similar. What matters is that the room does not drift,
    and the chain is what protects that."""
    out = []
    url = base.get("url") or upload(base["path"])
    for i, stage in enumerate(stages):
        dest = run_dir / f"{tag}_{i + 2}.jpg"
        got = None
        for model, url_key in EDIT_MODELS:
            try:
                args = {"prompt": stage,
                        url_key: [url] if url_key.endswith("s") else url}
                if canvas:
                    args["image_size"] = {"width": canvas[0], "height": canvas[1]}
                import fal_client
                res = fal_client.run(model, arguments=args)
                img = res["images"][0]
                _download(img["url"], dest)
                got = {"path": str(dest), "prompt": stage, "model": model,
                       "url": img.get("url", "")}
                break
            except Exception as e:
                print(f"  [factory] {model} failed on stage {i + 2}: {str(e)[:70]}")
        if not got:
            print(f"  [factory] stage {i + 2} did not render — chain stops here")
            break
        got["moved"] = round(frame_distance(
            out[-1]["path"] if out else base["path"], got["path"]), 1)
        out.append(got)
        url = got["url"] or upload(got["path"])     # the next stage builds on this one
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
CAROUSEL = (1080, 1350)   # 4:5 — the tallest shape the FEED shows uncropped
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


def fit_bold(texts: list[str], column: int, start: int, floor: int,
             max_lines: int = 1) -> int:
    """The largest bold size at which every one of `texts` fits `column`.

    One size for the whole card, decided by the longest line: sized per row,
    "BRUSHED NICKEL" would tower over "MOSS GREEN CHALKY LIMEWASH WALLS" and
    the card would read as a ransom note rather than a specification.

    With max_lines > 1 a name may wrap instead of shrinking, and wrapping is
    preferred: "moss green chalky limewash walls" fits one line at 34px on a
    1080 frame, which is a whisper, and two lines at 56px, which is the size
    this format is read at. Returns `floor` when even the wrap overflows."""
    size = start
    while size > floor:
        f = _bold_font(size)
        if f is None or all(
                all(f.getbbox(line)[2] <= column
                    for line in wrap_to(t, f, column, max_lines))
                for t in texts if t):
            return size
        size -= 2
    return floor


def wrap_to(text: str, font, column: int, max_lines: int = 2) -> list[str]:
    """`text` broken on spaces so no line exceeds `column`, at most max_lines.

    The last line absorbs whatever is left rather than being dropped: a
    specification that silently loses its final word is the bug this exists
    to end."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.getbbox(trial)[2] > column and len(lines) + 1 < max_lines:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or [text]


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
    hex_size = int(W * 0.028)
    swatch = int(W * 0.058)
    gap = int(W * 0.028)
    text_x = pad + swatch + int(W * 0.022)   # one column for every row, swatch or not
    # The column the names actually get, measured — not assumed. A material
    # name is as long as the material is specific ("moss green chalky limewash
    # walls"), and specificity is the whole point of this format, so the type
    # bends to the name rather than the name being cut off at the panel edge.
    column = (W - pad // 2) - text_x - int(W * 0.03)
    names = [n.upper() for n, _ in rows]
    name_size = fit_bold(names, column, int(W * 0.052), int(W * 0.032),
                         max_lines=2)
    name_f, hex_f = _bold_font(name_size), _label_font(hex_size)
    if name_f is None:
        img.save(dest)
        return str(dest)

    wrapped = [wrap_to(n, name_f, column) for n in names]
    step = int(name_size * 1.04)
    heights = [max(swatch, len(w) * step + (hex_size + 4 if hx else 0)) + gap
               for w, (_, hx) in zip(wrapped, rows)]
    panel_h = sum(heights) - gap + 2 * pad
    # above the app's own furniture in a 9:16 frame, low in a square one
    top = (int(H * 0.60) if H > W else H - panel_h - int(H * 0.06))
    top = min(top, H - panel_h - int(H * 0.04))

    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((pad // 2, top, W - pad // 2, top + panel_h),
                        radius=int(W * 0.028), fill=(12, 12, 12, 168))
    y = top + pad
    for (name, hexcode), lines, line_h in zip(rows, wrapped, heights):
        if hexcode:
            d.rounded_rectangle((pad, y, pad + swatch, y + swatch),
                                radius=int(swatch * 0.22),
                                fill=hexcode, outline=(255, 255, 255, 90), width=2)
        ty = y - 2
        for line in lines:
            d.text((text_x, ty), line, font=name_f, fill=(255, 255, 255, 245))
            ty += step
        if hexcode:
            d.text((text_x, ty + 2), hexcode, font=hex_f,
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
    # The hex is not decoration. A board that prints #3D5C42 next to a beige
    # band is worse than no board at all: it breaks the one promise this
    # format makes, that the strip explains the room you are about to see.
    bands = "; ".join(
        f"band {i+1} — {name}"
        + (f" in the exact colour {hexcode}" if hexcode else "")
        + ", natural macro texture"
        for i, (name, hexcode) in enumerate(pairs))
    return (f"full-frame vertical stack of {n} interior material samples as "
            f"equal horizontal bands, edge to edge, no gaps: {bands}. "
            f"photorealistic macro material photography, soft studio light, "
            f"rich tactile detail, no text, no logos, no watermark")


def _rgb(hexcode: str) -> tuple[int, int, int]:
    h = hexcode.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _band_drift(band, target: tuple[int, int, int]) -> float:
    from PIL import ImageStat

    mean = ImageStat.Stat(band.convert("RGB")).mean[:3]
    return sum((a - b) ** 2 for a, b in zip(mean, target)) ** 0.5


# How far a rendered band may sit from the colour its own label claims before
# it is pulled back. Measured in RGB distance: 60 is roughly "a different
# shade of the same colour", beyond that it is a different colour.
BAND_TOLERANCE = 60
BAND_PULL = 0.5          # how hard to pull, keeping the texture readable


def refit_bands(image_path: str, count: int, dest: Path,
                canvas: tuple[int, int]) -> str:
    """A band board re-cut to another shape, band for band.

    ImageOps.fit centre-crops the whole picture, so a 9:16 board of four
    equal bands taken to 4:5 keeps 195px of the first band and the last
    against 480px of the middle two: the outer materials come out as slivers,
    and their name plates — about 90px tall — overhang into the neighbouring
    band. Each band is fitted into its own row of the new canvas instead, so
    every material gets the same height whatever the aspect."""
    from PIL import Image, ImageOps

    src = Image.open(image_path).convert("RGB")
    W, H = canvas
    out = Image.new("RGB", canvas)
    count = max(1, count)
    for i in range(count):
        band = src.crop((0, int(i * src.height / count), src.width,
                         int((i + 1) * src.height / count)))
        top, bottom = int(i * H / count), int((i + 1) * H / count)
        out.paste(ImageOps.fit(band, (W, bottom - top), method=Image.LANCZOS),
                  (0, top))
    out.save(dest)
    return str(dest)


def correct_bands(image_path: str, pairs: list[tuple[str, str]],
                  dest: Path, canvas: tuple[int, int]) -> tuple[str, list[str]]:
    """Make each band the colour its label says it is.

    The renderer treats a hex code as a suggestion and often ignores it —
    a forest-green zellige comes back beige, and the strip then contradicts
    both its own caption and the room it introduces. Each band is measured
    against its stated colour and, when it has drifted, blended toward it
    hard enough to fix the hue while the texture still reads.

    Returns the path and a note per corrected band, so a run says out loud
    what it had to fix."""
    from PIL import Image, ImageOps

    img = ImageOps.fit(Image.open(image_path).convert("RGB"), canvas,
                       method=Image.LANCZOS)
    W, H = canvas
    notes = []
    for i, (name, hexcode) in enumerate(pairs):
        if not hexcode:
            continue
        top, bottom = int(i * H / len(pairs)), int((i + 1) * H / len(pairs))
        band = img.crop((0, top, W, bottom))
        target = _rgb(hexcode)
        drift = _band_drift(band, target)
        if drift <= BAND_TOLERANCE:
            continue
        flat = Image.new("RGB", band.size, target)
        img.paste(Image.blend(band, flat, BAND_PULL), (0, top))
        notes.append(f"{name} was {drift:.0f} from {hexcode} — pulled back")
    img.save(dest)
    return str(dest), notes


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
    pad = int(W * 0.055)
    hex_size = int(W * 0.028)
    name_size = fit_bold([n.upper() for n, _ in pairs], W - 2 * pad - 36,
                         int(W * 0.056), int(W * 0.032))
    name_f, hex_f = _bold_font(name_size), _label_font(hex_size)
    if name_f is None:
        img.save(dest)
        return str(dest)
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    band_h = H / len(pairs)
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


def carousel_frames(image_paths: list[str], labels: list[str],
                    run_dir: Path) -> list[str]:
    """The reel's own frames, re-cut as feed slides.

    A carousel is read, not watched: nothing arrives after this slide to
    explain it, so each one carries its own specification — the card style,
    burned after the crop so it sits where it was measured to sit. 4:5
    because a 9:16 slide is cropped on both ends in the feed, and the crop
    would take the ceiling and the floor a room shot lives on."""
    out = []
    for i, (src, label) in enumerate(zip(image_paths, labels)):
        dest = run_dir / f"slide-{i + 1}.jpg"
        if label:
            burn_spec_card(src, label, dest, CAROUSEL)
        else:
            from PIL import Image, ImageOps
            ImageOps.fit(Image.open(src).convert("RGB"), CAROUSEL,
                         method=Image.LANCZOS).save(dest)
        out.append(str(dest))
    return out


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


def board_running_order(frames: list[str], board_secs: float, room_secs: float,
                        hook_secs: float = 0.0
                        ) -> tuple[list[str], list[float], list[str]]:
    """The board format's frames, their holds, and the rooms it finishes on.

    `frames` arrives interleaved board, room, board, room… Returns the reel's
    running order, one duration per frame, and the room frames the carousel
    twin reuses.

    The rooms are picked out BEFORE the hook prepends them. After the prepend
    every second frame is a board, so a later `[1::2]` reads swatches — which
    is how the carousel twin would come to ship texture close-ups instead of
    rooms. Passing hook_secs=0 leaves the sequence alone."""
    rooms = frames[1::2]
    order = list(frames)
    durations = [board_secs, room_secs] * (len(frames) // 2)
    if hook_secs and len(rooms) > 1:
        # the payoff first: every finished room flashed before the first
        # board asks for patience
        order = list(rooms) + order
        durations = [hook_secs] * len(rooms) + durations
    return order, durations, rooms


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


# ── morph video: the transition IS the content ──────────────────
#
# Measured off the reel the operator sent on 2026-08-17: no scene cuts at
# all, the room changing continuously under a locked camera. A slideshow
# cannot fake that, and the operator said so in as many words — "artık
# slide show gibi değil düz video olması lazım". So the frames stop being
# the product and become the KEYFRAMES: what gets published is the video
# generated between them.
#
# Chosen on a bake-off run on 2026-08-17: the SAME two salon frames through
# three first-and-last-frame models, because a five-style reel buys five of
# these and the choice is repeated on every video we ever post.
#
#   pixverse v4.5 transition  $0.20   35s   720×1280 30fps, camera LOCKED
#   wan-2.1 flf2v 720p        $0.40   —     never returned in 25 minutes
#   kling v1.6 pro + tail     $0.475  191s  1080×1920, but it PANS: the
#                                           middle of the clip cuts to a
#                                           different corner of the room
#
# So the cheapest one is also the right one — a locked camera is the whole
# format, and pixverse's own output shape is exactly the reference reel's
# (720×1280, 30fps). Kling stays as the fallback because a panning
# transition still beats a missing one; wan is not in the list at all,
# since a model that hangs would stall the reel rather than degrade it.
MORPH_MODELS = [
    ("fal-ai/pixverse/v4.5/transition", 0.20,
     lambda a, b: {"first_image_url": a, "last_image_url": b,
                   "resolution": "720p", "duration": 5}),
    ("fal-ai/kling-video/v1.6/pro/image-to-video", 0.475,
     lambda a, b: {"image_url": a, "tail_image_url": b,
                   "duration": "5", "aspect_ratio": "9:16"}),
]

# THIS EXACT STRING IS THE ONE THAT WAS PROVEN. Do not edit it without
# buying a single transition and looking at the result — two attempts to
# improve it cost $2.00 between them and both came back with a sheet of
# white smoke sweeping the room:
#
#   "…as if a covering were being pulled off"  → the model drew a covering.
#   "…no smoke, no fog, no cloth, no wipe"     → naming smoke SUMMONED smoke;
#                                                video models routinely read
#                                                a negation as a subject.
#
# Say plainly what the surfaces do, name nothing you do not want to see, and
# let looks_wiped() below check the first clip before the other four are
# bought.
MORPH_PROMPT = ("the furniture, materials and finishes of the room transform "
                "smoothly in place from one interior style into the other, "
                "the camera is locked and does not move, no cuts, no people")


def mean_luminance(path: str) -> float:
    """How bright a still or a video frame is, 0-1."""
    from PIL import Image, ImageStat

    r, g, b = ImageStat.Stat(
        Image.open(path).convert("RGB").resize((64, 64))).mean[:3]
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


# How far above BOTH of its endpoints a transition may glow before it is a
# wipe rather than a re-skin. The smoke bursts measured 0.61 against
# keyframes of 0.20-0.47; a genuine re-skin never leaves the corridor
# between the two rooms by much, because it IS the two rooms.
WIPE_MARGIN = 0.10


def looks_wiped(clip: str, first: str, last: str,
                run_dir: Path | None = None) -> str:
    """"" if the transition is a re-skin, else why it is not — checked on the
    FIRST clip, before the other four are paid for.

    A video model asked for a material change will sometimes deliver a
    theatrical one instead: a sheet of white smoke sweeping the room, a
    curtain, a flash. It happened twice here, five clips at a time, and both
    times the operator paid for all five before anyone could see it. One
    clip is $0.20 and this costs nothing."""
    import subprocess
    import tempfile

    ceiling = max(mean_luminance(first), mean_luminance(last)) + WIPE_MARGIN
    tmp = Path(run_dir or tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    worst, at = 0.0, 0.0
    total = clip_seconds(clip)
    step = max(total / 12, 0.2)
    t = step
    while t < total:
        probe = tmp / f"_wipe{t:.1f}.png"
        subprocess.run([ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(clip),
                        "-frames:v", "1", str(probe)], capture_output=True)
        if probe.exists():
            lum = mean_luminance(str(probe))
            if lum > worst:
                worst, at = lum, t
            probe.unlink(missing_ok=True)
        t += step
    if worst > ceiling:
        return (f"the transition whites out at {at:.1f}s ({worst:.2f} "
                f"luminance against {ceiling - WIPE_MARGIN:.2f} for the "
                f"brighter of its two rooms) — the model drew a wipe, smoke "
                f"or a flash across the room instead of changing its "
                f"materials in place")
    return ""


def morph_clip(first_url: str, last_url: str, dest: Path,
               prompt: str = MORPH_PROMPT) -> tuple[str, str, float]:
    """One generated transition between two stills. Returns (path, model, cost).

    Falls through the chain on failure rather than dying: a five-morph reel
    that loses one transition to a provider hiccup should cost that
    transition, not the four already paid for."""
    import fal_client

    last_err = None
    for model, price, build in MORPH_MODELS:
        try:
            res = fal_client.run(model, arguments={"prompt": prompt,
                                                   **build(first_url, last_url)})
            url = (res.get("video") or {}).get("url") or res.get("video_url")
            _download(url, dest)
            return str(dest), model, price
        except Exception as e:
            last_err = e
            print(f"  [factory] morph via {model} failed: {str(e)[:80]}")
    raise RuntimeError(f"no morph model answered, last: {last_err}")


def clip_seconds(path: str) -> float:
    """A clip's real duration, read back rather than assumed.

    The generators are approximate — a "5 second" transition came back at
    5.37s — and the whole format is a rhythm, so retiming has to divide by
    what the file actually is."""
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
    if not m:
        raise RuntimeError(f"could not read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def retime(src: str, seconds: float, dest: Path,
           canvas: tuple[int, int] = VERTICAL, fps: int = 30) -> str:
    """A clip stretched or squeezed to an exact length, at delivery size.

    The generators' floor is five seconds and the reference's beat is three,
    so every transition is played faster than it was made. Done by changing
    presentation timestamps, not by dropping frames: the motion stays smooth,
    it just arrives sooner."""
    w, h = canvas
    factor = clip_seconds(src) / seconds
    subprocess.run([
        ffmpeg_bin(), "-y", "-i", str(src),
        "-filter_complex",
        f"[0:v]setpts=PTS/{factor:.6f},fps={fps},"
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1[v]",
        "-map", "[v]", "-an", "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(dest)], check=True, capture_output=True)
    return str(dest)


def still_clip(image: str, seconds: float, dest: Path,
               canvas: tuple[int, int] = VERTICAL, fps: int = 30) -> str:
    """The opening hold: the before-room, standing still, being looked at."""
    w, h = canvas
    subprocess.run([
        ffmpeg_bin(), "-y", "-loop", "1", "-i", str(image), "-t", f"{seconds:.3f}",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,"
               f"crop={w}:{h},setsar=1,fps={fps}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(dest)], check=True, capture_output=True)
    return str(dest)


def style_name(label: str) -> str:
    """The part of a label that goes on screen — "Art Deco", not the hex list.

    A label carries the style AND its materials so the carousel twin can
    print the specification. In the video there is no time to read that: the
    reference puts one or two words on the frame and the voice says the same
    two words."""
    return str(label or "").split("·")[0].strip()


def caption_png(text: str, dest: Path, canvas: tuple[int, int] = VERTICAL,
                height: float = 0.65, max_frac: float = 0.62) -> str:
    """One line of centred white type on transparency, ready to overlay.

    Drawn with PIL rather than ffmpeg's drawtext for the same reason the
    slideshow labels are: the static ffmpeg build that stands in when a box
    has no system ffmpeg ships without the drawtext filter, and losing the
    text loses the format.

    No plate behind it — the reference has none — but a soft shadow, because
    white type on a bright Scandinavian room is otherwise unreadable and the
    shadow is invisible where the room is dark."""
    from PIL import Image, ImageDraw, ImageFilter

    W, H = canvas
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    # The reference sets its style names at about 7% of the frame width —
    # confident, not shouting. The first version here started at 11.5% and
    # filled the frame edge to edge, which the operator read as too big.
    size = int(W * 0.072)
    if _bold_font(size) is None:
        layer.save(dest)
        return str(dest)

    # A style name is two words and stays big; an opening line is six and
    # would shrink to a whisper if it had to fit on one. It WRAPS instead —
    # the size is what makes it readable in the second and a half it has.
    def wrap(font, words: list[str]) -> list[str]:
        lines, line = [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if line and font.getbbox(trial)[2] > W * max_frac:
                lines.append(line)
                line = word
            else:
                line = trial
        return lines + [line] if line else lines

    words = text.split()
    while size > 26:
        font = _bold_font(size)
        lines = wrap(font, words)
        if len(lines) <= 2 and all(font.getbbox(x)[2] <= W * max_frac for x in lines):
            break
        size -= 3
    font = _bold_font(size)
    lines = wrap(font, words)

    d = ImageDraw.Draw(layer)
    step = int(size * 1.18)
    top = int(H * height) - (step * len(lines)) // 2
    placed = []
    for i, line in enumerate(lines):
        x0, y0, x1, y1 = d.textbbox((0, 0), line, font=font)
        placed.append((line, (W - (x1 - x0)) // 2 - x0, top + i * step - y0))
    shadow = Image.new("RGBA", canvas, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for line, x, y in placed:
        sd.text((x, y), line, font=font, fill=(0, 0, 0, 205))
    layer = Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)), layer)
    d = ImageDraw.Draw(layer)
    for line, x, y in placed:
        d.text((x, y), line, font=font, fill=(255, 255, 255, 255))
    layer.save(dest)
    return str(dest)


def burn_centre(image_path: str, text: str, dest: Path,
                canvas: tuple[int, int] = VERTICAL,
                height: float = 0.65) -> str:
    """A still with the video's own centred type on it — the cover frame.

    Reels opens on whatever frame sits at its default offset, and a morph
    reel's opening seconds are deliberately the room nobody wants. Without a
    cover chosen here, the thumbnail in the grid is the tired version."""
    from PIL import Image, ImageOps

    img = ImageOps.fit(Image.open(image_path).convert("RGB"), canvas,
                       method=Image.LANCZOS).convert("RGBA")
    layer = Image.open(caption_png(text, dest.with_suffix(".overlay.png"),
                                   canvas, height)).convert("RGBA")
    Image.alpha_composite(img, layer).convert("RGB").save(dest, quality=95)
    return str(dest)


def morph_timeline(lead_secs: float, secs_per_style: float,
                   n_styles: int, hold: float,
                   lead_is_style: bool = False) -> list[tuple[float, float]]:
    """When each style's name is on screen: (start, end) per style.

    A style's beat is its transition plus its arrival, and the name belongs
    to the arrival — put it on the transition and it labels a room that is
    still half the previous style. Measured on the reference: the name lands
    when the new room is fully there and stays about 1.2s.

    `lead_secs` is whatever is on screen before the first transition. With
    lead_is_style that is the first style itself rather than a before-room,
    so its name has no transition to wait for — it is shown as the reel
    opens, and every later name still lands on its own arrival."""
    out = []
    if lead_is_style:
        out.append((0.25, round(0.25 + hold, 3)))
        n_styles -= 1
    for i in range(n_styles):
        end = lead_secs + (i + 1) * secs_per_style
        out.append((round(end - hold, 3), round(end, 3)))
    return out


# The reference reads its on-screen words in a low voice and nothing else,
# and that is all this says too — five style names, each landing as its room
# arrives. Kokoro was first here because it is a twentieth of the price, and
# the operator's verdict on it was "boğuk" — muffled. For sixty characters a
# reel the price difference is under a cent, so the HD model leads now and
# kokoro is the fallback.
NAMING_VOICES = [
    ("fal-ai/minimax/speech-02-hd",
     lambda text: {"text": text,
                   "voice_setting": {"voice_id": "Deep_Voice_Man",
                                     "speed": 0.92, "vol": 1.0, "pitch": 0}}),
    ("fal-ai/kokoro/american-english",
     lambda text: {"prompt": text, "voice": "am_michael"}),
    ("fal-ai/kokoro/american-english",
     lambda text: {"prompt": text, "voice": "am_onyx"}),
]


def say_name(text: str, dest: Path) -> tuple[str, str]:
    """One style's name, spoken. Returns (path, model).

    Generated per name rather than as one script so each can be placed on
    its own frame: the operator's instruction was that the voice says the
    style AS the style appears, and a single take cannot be trusted to fall
    on the beat."""
    import fal_client

    last_err = None
    for model, build in NAMING_VOICES:
        try:
            res = fal_client.run(model, arguments=build(text))
            url = (res.get("audio") or {}).get("url") or res.get("audio_url")
            _download(url, dest)
            return str(dest), model
        except Exception as e:
            last_err = e
            print(f"  [factory] voice via {model} failed: {str(e)[:70]}")
    raise RuntimeError(f"no TTS model answered, last: {last_err}")


# Length is not a shared parameter: lyria2 returns its own fixed length and
# stable-audio takes seconds_total. Sending the wrong key costs a failed
# call before the fallback, so each model gets its own arguments.
MUSIC_MODELS = [
    ("fal-ai/lyria2", lambda brief, secs: {"prompt": brief,
                                           "negative_prompt": "vocals, drums, percussion"}),
    ("fal-ai/stable-audio", lambda brief, secs: {"prompt": brief,
                                                 "seconds_total": int(secs) + 2}),
]
MUSIC_BRIEF = ("calm minimal ambient instrumental bed for an interior design "
               "reel, warm analogue pads, soft slow pulse, no drums, no vocals, "
               "unobtrusive, loopable")


def music_bed(seconds: float, dest: Path, brief: str = MUSIC_BRIEF
              ) -> tuple[str, str]:
    """A quiet instrumental floor under the whole reel. Returns (path, model).

    Deliberately generated rather than licensed: the operator publishes these
    reels by hand and drops a trending track on at that point, so this exists
    so the draft does not sound broken in review — it is not the audio that
    goes out."""
    import fal_client

    last_err = None
    for model, build in MUSIC_MODELS:
        try:
            res = fal_client.run(model, arguments=build(brief, seconds))
            url = (res.get("audio") or {}).get("url") or res.get("audio_url")
            _download(url, dest)
            return str(dest), model
        except Exception as e:
            last_err = e
            print(f"  [factory] music via {model} failed: {str(e)[:80]}")
    raise RuntimeError(f"no music model answered, last: {last_err}")


def morph_audio(names: list[tuple[str, float]], music: str | None,
                total: float, dest: Path,
                music_db: float = -19.0) -> str:
    """Spoken style names dropped onto their own timestamps, over the bed.

    Each name is delayed to the exact moment its room arrives instead of
    being read as one continuous script — that is the difference between the
    voice naming what you are looking at and the voice talking over it."""
    inputs, filters, mix = [], [], []
    for i, (path, at) in enumerate(names):
        inputs += ["-i", str(path)]
        ms = int(max(at, 0) * 1000)
        filters.append(f"[{i}:a]adelay={ms}|{ms},volume=1.6[n{i}]")
        mix.append(f"[n{i}]")
    idx = len(names)
    if music:
        inputs += ["-i", str(music)]
        filters.append(f"[{idx}:a]volume={music_db}dB,"
                       f"afade=t=out:st={max(total - 1.2, 0):.2f}:d=1.2[bed]")
        mix.append("[bed]")
        idx += 1
    # a silent floor guarantees a full-length stereo track even if every
    # other input is shorter — Instagram treats a file with no audio stream
    # as not-a-video and hides its own audio tools from the operator
    inputs += ["-f", "lavfi", "-i",
               "anullsrc=channel_layout=stereo:sample_rate=44100"]
    filters.append(f"[{idx}:a]atrim=0:{total:.3f}[floor]")
    mix.append("[floor]")
    # aformat AFTER the mix: amix takes its layout from the first input, and
    # a mono TTS clip arriving first turns the whole reel's track mono — a
    # detail nobody notices in review and every phone speaker does
    filters.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=longest:"
                   f"dropout_transition=0,atrim=0:{total:.3f},"
                   f"aresample=44100,aformat=channel_layouts=stereo[a]")
    subprocess.run([ffmpeg_bin(), "-y", *inputs,
                    "-filter_complex", ";".join(filters), "-map", "[a]",
                    "-c:a", "aac", "-b:a", "160k", str(dest)],
                   check=True, capture_output=True)
    return str(dest)


def morph_command(clips: list[str], overlays: list[tuple[str, float, float]],
                  audio: str | None, out_path: Path,
                  canvas: tuple[int, int] = VERTICAL, fps: int = 30) -> list[str]:
    """The ffmpeg argv for the finished reel, split out so it is inspectable.

    Concatenation and every text overlay in ONE encode: written out as
    intermediate files instead, each label would cost another generation of
    h264 and the type would soften on the frames it matters most on."""
    w, h = canvas
    inputs, filters = [], []
    for p in clips:
        inputs += ["-i", str(p)]
    n = len(clips)
    for i in range(n):
        filters.append(f"[{i}:v]scale={w}:{h},setsar=1,fps={fps}[c{i}]")
    filters.append("".join(f"[c{i}]" for i in range(n))
                   + f"concat=n={n}:v=1:a=0[base]")
    last = "base"
    for j, (png, start, end) in enumerate(overlays):
        inputs += ["-i", str(png)]
        tag = f"o{j}"
        filters.append(
            f"[{last}][{n + j}:v]overlay=0:0:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{tag}]")
        last = tag
    cmd = [ffmpeg_bin(), "-y", *inputs]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    if audio:
        cmd += ["-map", f"{n + len(overlays)}:a", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-shortest",
            "-movflags", "+faststart", str(out_path)]
    return cmd


def make_morph_video(before: str | None, styled: list[str], labels: list[str],
                     run_dir: Path, before_secs: float = 2.2,
                     secs_per_style: float = 3.0, hold: float = 1.2,
                     opening_line: str = "", voice: bool = True,
                     music: bool = True,
                     canvas: tuple[int, int] = VERTICAL) -> dict:
    """The whole reel: one room becoming several named ones, without a cut.

    `before` is the tired room the reel opens on, and it is optional. Without
    it the reel opens on the FIRST STYLE and morphs through the rest — the
    operator's call for the villa hall: "oranin bos hali olmayacak, 6 farkli
    tarz istiyorum". That costs one transition fewer than it looks: six
    styles and no before is five morphs, not six.

    Returns the path plus what it cost and what it had to skip, because the
    spend here is real money per transition and a run that quietly dropped
    one should say so."""
    from PIL import Image, ImageOps

    run_dir.mkdir(parents=True, exist_ok=True)
    # every keyframe cropped to the delivery shape BEFORE it is uploaded: the
    # editor returns a slightly different aspect from the renderer, and a
    # transition between two shapes pans while it morphs — the one camera
    # move this format promises never happens
    frames = []
    for i, src in enumerate(([before] if before else []) + list(styled)):
        dest = run_dir / f"key{i}.jpg"
        ImageOps.fit(Image.open(src).convert("RGB"), canvas,
                     method=Image.LANCZOS).save(dest, quality=95)
        frames.append(str(dest))
    urls = [upload(p) for p in frames]

    clips, spend, models, notes = [], 0.0, [], []
    clips.append(still_clip(frames[0], before_secs,
                            run_dir / "hold.mp4", canvas))
    for i in range(len(frames) - 1):
        raw = run_dir / f"morph{i + 1}-raw.mp4"
        try:
            path, model, price = morph_clip(urls[i], urls[i + 1], raw)
        except Exception as e:
            # a missing transition is a jump cut in a format whose whole
            # promise is that there are none — say it out loud
            notes.append(f"transition {i + 1} was not generated ({str(e)[:60]})")
            clips.append(still_clip(frames[i + 1], secs_per_style,
                                    run_dir / f"morph{i + 1}.mp4", canvas))
            continue
        spend += price
        models.append(model)
        # THE FIRST CLIP IS THE SAMPLE. Two prompt changes shipped a sheet of
        # white smoke across every transition, and both times all five were
        # bought before anyone could see one. Checking here caps that mistake
        # at $0.20 — and the check is free.
        if i == 0:
            wiped = looks_wiped(path, frames[0], frames[1], run_dir)
            if wiped:
                raise RuntimeError(
                    f"stopping after ONE transition (${price:.2f}) instead of "
                    f"{len(frames) - 1}: {wiped}. The keyframes are fine and "
                    f"still in {run_dir} — fix the transition prompt and "
                    f"rebuild with --from-run.")
        clips.append(retime(path, secs_per_style,
                            run_dir / f"morph{i + 1}.mp4", canvas))

    total = before_secs + secs_per_style * (len(frames) - 1)
    names = [style_name(x) for x in labels][:len(styled)]
    beats = morph_timeline(before_secs, secs_per_style, len(styled), hold,
                           lead_is_style=not before)
    overlays = []
    if opening_line and before:
        # it belongs to the before-room, which is the only frame with no name
        # of its own; opening on a style, the style's name IS the opening
        overlays.append((caption_png(opening_line, run_dir / "open.png", canvas),
                         0.25, before_secs))
    # numbered, never named: two styles sharing a prefix ("Mid-Century" and
    # "Mid-Century Modern") would write to the same file and the second room
    # would wear the first one's name
    for i, ((start, end), name) in enumerate(zip(beats, names)):
        if name:
            overlays.append((caption_png(name, run_dir / f"lab{i + 1}.png",
                                         canvas), start, end))

    spoken, bed = [], None
    if voice:
        for i, ((start, _), name) in enumerate(zip(beats, names)):
            if not name:
                continue
            try:
                path, model = say_name(name, run_dir / f"say{i + 1}.mp3")
                spoken.append((path, start))
                models.append(model)
            except Exception as e:
                notes.append(f'"{name}" was not spoken ({str(e)[:50]})')
    if music:
        try:
            bed, model = music_bed(total, run_dir / "bed.mp3")
            models.append(model)
        except Exception as e:
            notes.append(f"no music bed ({str(e)[:50]})")
    audio = morph_audio(spoken, bed, total, run_dir / "mix.m4a")

    out = run_dir / "morph.mp4"
    subprocess.run(morph_command(clips, overlays, audio, out, canvas),
                   check=True, capture_output=True)
    return {"path": str(out), "seconds": total, "spend": round(spend, 3),
            "models": sorted(set(models)), "notes": notes}


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
