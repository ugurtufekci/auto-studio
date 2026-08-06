"""Persona brain — handbook pages 06/07, miniature.

Signal → brief → caption, all in the persona's voice, all constrained by
the mini-bible in config/persona.yaml. The brain never handles disclosure —
that is enforced mechanically at the publish gate (publisher.py), exactly
as the handbook demands.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_persona() -> dict:
    with open(CONFIG_DIR / "persona.yaml") as f:
        return yaml.safe_load(f)


PROMPT = """You are the content brain of "{name}" — {tagline}.

PREMISE: {premise}

VOICE RULES:
- register: {register}
- rhythm: {rhythm}
- emoji: {emoji}
- hashtags: {hashtags}
- NEVER: {never}
- instead: {instead}

VISUAL WORLD (all imagery must live here):
- palette: {palette}
- recurring places: {world}
- every image prompt MUST end with this style suffix: "{style_suffix}"
- never in images: {avoid}

TODAY'S SIGNAL (a trend wave detected across sources this morning):
- topic: {sig_topic} [{sig_type}]
- what it is: {sig_summary}
- why now: {sig_why}

TASK: produce a {fmt} brief riding this signal, as this persona.

Format notes:
- image_post: 1 image prompt, caption ≤ 220 characters (hard limit).
- slideshow_video: 4 image prompts forming a tiny visual sequence
  (vary angle/scene, same world), a 20-30 word voiceover script in the
  persona's voice, caption ≤ 200 characters.

The caption must read like the persona thought it, not like a report about
a trend. No "trending now" meta-talk. Include 2-3 lowercase niche hashtags
at the end per hashtag policy. Remember she observes the culture — she
never claims to drink, taste, or physically do anything.

Return STRICT JSON, no markdown fences:
{{"premise": "one sentence — what this post is",
  "angle": "how the persona takes the signal",
  "mood": "2-4 words",
  "caption": "the post text with hashtags",
  "alt_text": "one-sentence image description for accessibility",
  "voiceover_script": "only for slideshow_video, else empty string",
  "image_prompts": ["...", "..."]}}"""


def make_brief(signal: dict, fmt: str, model: str | None = None,
               avoid_captions: list[str] | None = None) -> dict:
    from studio import llm

    # persona voice is the audience-facing text — bump to a stronger model via
    # BRAIN_MODEL in .env if captions ever feel flat (handbook: quality tier
    # only where the audience reads it)
    model = model or os.environ.get("BRAIN_MODEL", llm.DEFAULT_MODEL)

    p = load_persona()
    ident, voice, vis = p["identity"], p["voice"], p["visual_grammar"]
    prompt = PROMPT.format(
        name=ident["name"], tagline=ident["tagline"], premise=ident["premise"],
        register=voice["register"], rhythm=voice["sentence_rhythm"],
        emoji=voice["emoji_policy"], hashtags=voice["hashtag_policy"],
        never="; ".join(voice["never_says"]),
        instead="; ".join(voice["says_instead"]),
        palette=vis["palette"], world="; ".join(vis["recurring_world"]),
        style_suffix=vis["style_suffix"].strip(), avoid=vis["avoid"],
        sig_topic=signal["topic"], sig_type=signal["signal_type"],
        sig_summary=signal["summary"], sig_why=signal["why_now"],
        fmt=fmt,
    )
    if avoid_captions:
        listing = "\n".join(f'- "{c}"' for c in avoid_captions)
        prompt += (f"\n\nIMPORTANT: these captions were already used recently — "
                   f"write something clearly different in structure and wording:\n{listing}")
    reply = llm.complete(prompt, model=model, max_tokens=1500)
    brief = llm.extract_json(reply)
    brief["format"] = fmt
    brief["model"] = model

    # mechanical guards — never trust one layer
    expected = 1 if fmt == "image_post" else 4
    brief["image_prompts"] = (brief.get("image_prompts") or [])[:expected]
    if len(brief["image_prompts"]) < expected:
        raise ValueError(f"brain returned {len(brief['image_prompts'])} prompts, need {expected}")
    suffix = vis["style_suffix"].strip()
    brief["image_prompts"] = [
        ip if suffix.lower()[:20] in ip.lower() else f"{ip}, {suffix}"
        for ip in brief["image_prompts"]
    ]
    return brief


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    demo_signal = {
        "topic": "espresso tonic summer wave",
        "signal_type": "product",
        "summary": "Iced espresso tonic is having its August moment across cafés.",
        "why_now": "multiple bsky posts and industry articles this week",
    }
    b = make_brief(demo_signal, "image_post")
    print(json.dumps(b, indent=2))
