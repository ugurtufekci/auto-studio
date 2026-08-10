"""Per-platform rendition layer — handbook's "one brief, many cuts".

The brief carries the message; this module only decides how it is cut for each
destination. One LLM call produces every rendition at once, so the variants stay
coherent with each other instead of drifting apart.

YouTube is the reason this exists as its own step: it wants a title, a
description and search tags, not a caption.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

PROMPT = """You are the distribution layer of an automated content studio running
a disclosed-AI persona.

The brief below was already written for this persona. Do NOT change the message,
the angle, or the persona's voice. Your only job is to CUT it for each platform's
native shape and limits.

PERSONA VOICE (must survive every cut):
- register: {register}
- rhythm: {rhythm}
- emoji: {emoji}
- never: {never}
- she observes the culture; she never claims to drink, taste or physically do anything

THE BRIEF:
- premise: {premise}
- angle: {angle}
- mood: {mood}
- the canonical caption already written: "{caption}"
- what the imagery shows: {alt}

TARGETS — produce one rendition per platform, respecting each shape:
{targets}

Rules:
- Never exceed a stated limit. Counting matters; shorter is safer than clipped.
- Do NOT append any AI-disclosure line — the publisher adds it mechanically.
- Each rendition must read as if written for that platform, not as a truncation
  of another. Different sentence structure, not the same sentence trimmed.
- For platforms wanting text: return the field "text".
- For youtube: return "title", "description" and "tags" (array of lowercase
  search terms, no # symbols).

Return STRICT JSON only, no markdown fences, keyed by platform:
{{"telegram": {{"text": "..."}}, "youtube": {{"title": "...", "description": "...",
"tags": ["..."]}}}}"""


def load_formats() -> dict:
    with open(CONFIG_DIR / "platform_formats.yaml") as f:
        return yaml.safe_load(f)


def _targets_block(platforms: list[str], fmts: dict) -> str:
    lines = []
    for p in platforms:
        f = fmts.get(p)
        if not f:
            continue
        if "title" in f.get("fields", []):
            limits = (f"title ≤ {f.get('title_limit')} chars, "
                      f"description ≤ {f.get('description_limit')} chars, "
                      f"up to {f.get('tags_max')} tags")
        else:
            limits = f"text ≤ {f.get('text_limit')} chars"
        lines.append(f"- {p} ({limits}): {' '.join(f['style'].split())}")
    return "\n".join(lines)


def _fallback(brief: dict, platform: str, fmts: dict) -> dict:
    """Mechanical cut if the model output is unusable for one platform."""
    f = fmts.get(platform, {})
    if "title" in f.get("fields", []):
        title = brief["premise"][:f.get("title_limit", 90)].rstrip(" .,")
        return {"title": title,
                "description": f"{brief['premise']}\n\n{brief['caption']}",
                "tags": []}
    limit = f.get("text_limit", 300)
    text = brief["caption"]
    return {"text": text if len(text) <= limit else text[:limit - 1].rstrip() + "…"}


def renditions(brief: dict, platforms: list[str],
               model: str | None = None,
               persona_id: str | None = None) -> dict:
    """{platform: {text} | {title, description, tags}} for every platform."""
    from studio import llm
    from studio import persona as persona_cfg

    fmts = load_formats()
    known = [p for p in platforms if p in fmts]
    out = {p: _fallback(brief, p, fmts) for p in platforms}
    if not known:
        return out

    persona = persona_cfg.load(persona_id)
    voice = persona["voice"]
    prompt = PROMPT.format(
        register=voice["register"], rhythm=voice["sentence_rhythm"],
        emoji=voice["emoji_policy"], never="; ".join(voice["never_says"]),
        premise=brief["premise"], angle=brief["angle"], mood=brief["mood"],
        caption=brief["caption"], alt=brief.get("alt_text", ""),
        targets=_targets_block(known, fmts),
    )
    model = model or os.environ.get("ADAPT_MODEL", llm.DEFAULT_MODEL)
    try:
        data = llm.extract_json(llm.complete(prompt, model=model, max_tokens=2000))
    except Exception as e:
        print(f"  [adapt] rendition call failed ({str(e)[:80]}) — mechanical cuts")
        return out

    for p in known:
        r = data.get(p)
        if not isinstance(r, dict):
            continue
        f = fmts[p]
        if "title" in f.get("fields", []):
            title = str(r.get("title", "")).strip()[:f["title_limit"]]
            desc = str(r.get("description", "")).strip()[:f["description_limit"]]
            tags = [str(t).lstrip("#").strip().lower()
                    for t in (r.get("tags") or []) if str(t).strip()][:f["tags_max"]]
            if title and desc:
                out[p] = {"title": title, "description": desc, "tags": tags}
        else:
            text = str(r.get("text", "")).strip()
            if text:
                out[p] = {"text": text[:f["text_limit"]]}
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    demo = {
        "premise": "Café design is shifting from sterile minimalism to warm industrial rooms.",
        "angle": "Mara reads the shift as cafés admitting they are social spaces, not counters.",
        "mood": "observant, warm",
        "caption": "austere is softening into intention. marble, wood, morning light—rooms "
                   "designed to linger. #caféculture #thirdplaces",
        "alt_text": "A café interior with exposed brick, communal wooden table and morning light.",
    }
    r = renditions(demo, ["telegram", "mastodon", "x", "instagram", "youtube"])
    for p, v in r.items():
        print(f"\n── {p} ──")
        for k, val in v.items():
            print(f"  {k}: {val}")
