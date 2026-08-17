"""Named video formats — the styles the studio knows how to shoot.

A format is a STYLE with a name: what the frames are, how they are paced,
whether the text lives on the picture or on its own board, how the images
are rendered. It lives in config/formats/<id>.yaml so a new style is a new
file, not a new branch in the runner.

The split from persona is deliberate. A persona is WHO is speaking — voice,
palette, what she notices. A format is WHAT KIND OF VIDEO this is. Several
personas can shoot the same format, and one persona shoots several: June
posts a material-board reel today and a colourway one tomorrow without
either of them being "June's settings".
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from studio import persona

FORMAT_DIR = Path(__file__).resolve().parent.parent / "config" / "formats"


@lru_cache(maxsize=16)
def load(format_id: str) -> dict:
    """One format by id. Raises with the known names when it does not exist —
    a typo in --style should say what the alternatives are, not fail deep
    inside the renderer with a missing key."""
    path = FORMAT_DIR / f"{format_id}.yaml"
    if not path.exists():
        known = ", ".join(available()) or "none configured"
        raise ValueError(f"unknown style '{format_id}' — known styles: {known}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("id", format_id)
    return cfg


def available() -> list[str]:
    return sorted(p.stem for p in FORMAT_DIR.glob("*.yaml"))


def catalogue() -> list[dict]:
    """Every style, for a console list or an operator picker."""
    out = []
    for fid in available():
        cfg = load(fid)
        out.append({"id": fid, "name": cfg.get("name", fid),
                    "tagline": cfg.get("tagline", ""),
                    "description": str(cfg.get("description", "")).strip()})
    return out


def for_persona(persona_id: str | None, requested: str = "") -> dict | None:
    """The format this run should shoot.

    An explicit --style wins and is checked against what the persona is
    allowed to shoot: a style is a commitment about the account's look, so
    pointing a persona at one it has not adopted should be a visible error
    rather than a surprise in the feed. With nothing requested the persona's
    default is used, and a persona that has adopted no format at all gets
    None — its own config still describes its slideshows, exactly as before
    formats existed.
    """
    cfg = (persona.load(persona_id).get("content") or {}).get("formats") or {}
    allowed = [str(x) for x in (cfg.get("allowed") or [])]
    if requested:
        if allowed and requested not in allowed:
            raise ValueError(
                f"persona '{persona_id}' has not adopted style '{requested}' — "
                f"it shoots: {', '.join(allowed)}")
        return load(requested)
    default = str(cfg.get("default") or "")
    return load(default) if default else None


def settings(fmt: dict | None, persona_id: str | None) -> dict:
    """Delivery settings for the assembly, format first, persona as fallback.

    Personas that predate formats (Mara's Telegram slideshows) keep their own
    slideshow_* keys and are untouched by any of this."""
    content = persona.load(persona_id).get("content") or {}
    fmt = fmt or {}

    def pick(fmt_key: str, persona_key: str, default):
        if fmt_key in fmt:
            return fmt[fmt_key]
        return content.get(persona_key, default)

    return {
        "aspect": str(pick("aspect", "slideshow_aspect", "square")),
        "label_style": str(pick("label_style", "slideshow_label_style", "chip")),
        "cut": str(pick("cut", "slideshow_cut", "")),
        "secs_per_frame": float(pick("secs_per_frame", "slideshow_secs_per_frame", 3.5)),
        "board_secs": float(pick("board_secs", "slideshow_board_secs", 1.1)),
        "voiceover": bool(pick("voiceover", "slideshow_voiceover", True)),
        # what KIND of voice, when there is one: "script" reads the brief's
        # voiceover_script over the whole video, "names" says only each
        # frame's style name as that frame arrives. Kept as its own key
        # because `voiceover` above is a yes/no the rest of the pipeline
        # already branches on, and bool("names") is True either way.
        "voice_mode": str(fmt.get("voiceover") if isinstance(fmt.get("voiceover"), str)
                          else "script"),
        "image_mode": str(fmt.get("image_mode", "t2i")),
        "i2i_strength": float(fmt.get("i2i_strength", 0.65)),
        "frames": list(fmt.get("frames") or [4, 6]),
        "carousel_twin": bool(fmt.get("carousel_twin", False)),
        "hook": str(fmt.get("hook", "")),
        "hook_secs": float(fmt.get("hook_secs", 0.28)),
        # ── morph styles ────────────────────────────────────────
        # "cut" assembles stills; "morph" pays a video model to generate the
        # transition between each pair, which is the whole difference between
        # a slideshow and the reels this studio was asked to match.
        "assembly": str(fmt.get("assembly", "cut")),
        "before_frame": bool(fmt.get("before_frame", False)),
        "before_secs": float(fmt.get("before_secs", 2.2)),
        "label_hold": float(fmt.get("label_hold", 1.2)),
        "label_height": float(fmt.get("label_height", 0.65)),
        "music": bool(fmt.get("music", False)),
    }
