"""Persona brain — handbook pages 06/07, miniature.

Signal → brief → caption, all in the persona's voice, all constrained by
the mini-bible in config/persona.yaml. The brain never handles disclosure —
that is enforced mechanically at the publish gate (publisher.py), exactly
as the handbook demands.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
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

PRODUCTION RULES — non-negotiable, they decide whether accounts survive:

1. GENERIC SUBJECTS, NEVER REAL ONES. Every image we publish is synthetic, so
   it must depict a KIND of thing, never a specific verifiable one. Write "a
   granite alpine lake at sunrise", never "Lake Louise"; "a sunlit corner
   café", never a named café; "a mid-century living room", never a named
   designer's actual room. Never a real person, a real event, a real
   brand's product, or a place a viewer could look up and find our picture is
   not it. A signal will often name real places — that is the source's
   language, not ours. Take the AESTHETIC from the signal and leave the
   proper nouns behind. A fabricated depiction of a real subject is a lie
   even when it carries our AI disclosure, and it is what gets a synthetic
   account reported and removed.

2. ONE POST, ONE IDEA OF ITS OWN. The `angle` is the actual product here: a
   specific editorial point of view a person could disagree with. Never a
   caption that could sit under any image in the category, never a template
   with the nouns swapped. Platforms now demonetise mass-produced,
   interchangeable content by name — the defence is that each post carries a
   thought, not that it is pretty.

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


# Region and period words are broad enough to stay generic: "a Western US
# alpine lake" describes a kind of place, "Grand Canyon" names one.
GENERIC_PROPER = {
    "us", "usa", "america", "american", "europe", "european", "nordic",
    "scandinavian", "mediterranean", "asia", "asian", "african", "alpine",
    "atlantic", "pacific", "arctic", "western", "eastern", "northern",
    "southern", "midwest", "midwestern", "west", "east", "north", "south",
    "coast", "coastal", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "ai",
}


@lru_cache(maxsize=1)
def category_vocabulary() -> frozenset[str]:
    """Words our own category configs use to describe KINDS of things.

    Source titles are written in title case ("Nature Shapes Every Room"), so
    ordinary category vocabulary arrives capitalised and would otherwise read
    as a real named subject. Anything in visual_keywords or a category label
    is by definition generic — and sourcing the list from the configs means a
    new category widens it without touching this module."""
    words: set[str] = set()
    for path in (CONFIG_DIR / "categories").glob("*.yaml"):
        try:
            cfg = yaml.safe_load(path.read_text()) or {}
        except Exception:
            continue
        for phrase in (cfg.get("visual_keywords") or []) + [cfg.get("label") or ""]:
            words.update(w for w in re.split(r"\W+", str(phrase).lower()) if len(w) > 2)
    return frozenset(words)


def signal_proper_nouns(signal: dict) -> set[str]:
    """Real named subjects the signal talks about — places, venues, brands.

    Capitalised runs are collected only from non-sentence-initial positions,
    so an ordinary capitalised first word ("Cozy summer nooks") is not
    mistaken for a proper noun. Region and period words are excluded as
    generic."""
    found: set[str] = set()
    # each field is its own text: joining them first would bury a field's
    # opening word mid-sentence, and ordinary capitalised openers ("Granite
    # basins…", "Warm corners.") would read as names
    for field in ("topic", "summary", "why_now"):
        for sentence in re.split(r"[.!?;:\n]", str(signal.get(field) or "")):
            # drop the first token: its capitalisation carries no information
            tokens = sentence.split()[1:]
            run: list[str] = []
            for tok in tokens:
                word = re.sub(r"^\W+|\W+$", "", tok)
                if re.match(r"^[A-Z][A-Za-z'’]+$", word):
                    run.append(word)
                    continue
                if run:
                    found.add(" ".join(run))
                    run = []
            if run:
                found.add(" ".join(run))
    return {f for f in found if f.lower() not in GENERIC_PROPER}


def real_subject_leaks(signal: dict, image_prompts: list[str]) -> list[str]:
    """Named subjects that travelled from the signal into an image prompt.

    Every image we publish is synthetic, so depicting a specific real place,
    venue or brand fabricates a verifiable subject — a lie the AI disclosure
    does not cure, and the fastest route to a reported account. Signals name
    real places constantly; the brief must take the aesthetic and leave the
    proper nouns."""
    joined = " ".join(image_prompts).lower()
    leaks = set()
    # a run like "Copenhagen NYC Boston" is several names in a row — every
    # token is its own real subject, so check them all, not just the first
    for noun in signal_proper_nouns(signal):
        for word in noun.split():
            w = word.lower()
            if len(w) < 3 or w in GENERIC_PROPER or w in category_vocabulary():
                continue
            if re.search(rf"\b{re.escape(w)}\b", joined):
                leaks.add(word)
    return sorted(leaks)


def make_brief(signal: dict, fmt: str, model: str | None = None,
               avoid_captions: list[str] | None = None,
               avoid_subjects: list[str] | None = None) -> dict:
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
    if avoid_subjects:
        prompt += (f"\n\nREJECTED: your last attempt put these real named subjects into "
                   f"image prompts: {', '.join(avoid_subjects)}. Those name specific "
                   f"verifiable things and we only publish synthetic imagery — describe "
                   f"the KIND of place instead, with no proper nouns anywhere in the "
                   f"image prompts.")
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
