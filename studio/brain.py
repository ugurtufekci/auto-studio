"""Persona brain — handbook pages 06/07, miniature.

Signal → brief → caption, all in one persona's voice, all constrained by that
persona's mini-bible in config/personas/<id>.yaml. The brain never handles
disclosure — that is enforced mechanically at the publish gate
(publisher.py), exactly as the handbook demands.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

from studio import persona

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Kept so callers can keep saying load_persona(); the identity itself lives in
# studio/persona.py, which resolves it per id.
load_persona = persona.load


PROMPT = """You are the content brain of "{name}" — {tagline}.

PREMISE: {premise}

VOICE RULES:
- register: {register}
- rhythm: {rhythm}
- emoji: {emoji}
- hashtags: {hashtags}
- NEVER: {never}
- instead: {instead}{extra_voice}

VISUAL WORLD (all imagery must live here):
- palette: {palette}
- recurring places: {world}
- every image prompt MUST end with this style suffix: "{style_suffix}"
- never in images: {avoid}{extra_visual}

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

3. WRITTEN TO BE FOUND. Instagram search reads CAPTIONS now, not only
   hashtags, and a post that names what it shows keeps earning views for
   months after the feed has moved on. So the caption must contain, in the
   persona's own voice and reading as a sentence she would write anyway, the
   plain words someone would type to find this: the room, the material, the
   decision ("powder room", "kitchen cabinet colours", "green tile
   bathroom"). Never a keyword list, never a phrase bolted on at the end —
   if it does not sound like her, it is wrong. The alt text carries the same
   plain description for the same reason: it is read by search and by people
   using screen readers, and both deserve the specific version.

TODAY'S SIGNAL (a trend wave detected across sources this morning):
- topic: {sig_topic} [{sig_type}]
- what it is: {sig_summary}
- why now: {sig_why}

TASK: produce a {fmt} brief riding this signal, as this persona.

Format notes:
- image_post: 1 image prompt, caption ≤ 220 characters (hard limit).
- slideshow_video: do NOT write the image prompts yourself. Return
  `base_scene` plus 4-6 `frame_swaps`, and they are assembled into prompts
  mechanically — that is what guarantees every frame is the same room.
  · `base_scene`: {base_scene_rule}
  · each `frame_swaps` entry is {{"change": "...", "label": "..."}}.
    `change`: {change_rule}
    `label`: {label_rule}
  A SLIDESHOW STRUCTURE block above is mandatory and governs what the room
  is and how bold the swaps must be. Voiceover script only if the persona
  uses voice, else empty string. Caption ≤ 200 characters.

The caption must read like the persona thought it, not like a report about
a trend. No "trending now" meta-talk. Include 2-3 lowercase niche hashtags
at the end per hashtag policy. Remember she observes the culture — she
never claims to drink, taste, or physically do anything.

Return STRICT JSON, no markdown fences:
{{"premise": "one sentence — what this post is",
  "angle": "how the persona takes the signal",
  "mood": "2-4 words",
  "caption": "the post text with hashtags",
  "alt_text": "one plain sentence naming the room, materials and colours",
  "voiceover_script": "only for slideshow_video, else empty string",
  "base_scene": "slideshow only — the room once, no changeable finishes",
  "frame_swaps": [{{"change": "this frame's finishes only", "label": "1 · … #HEX"}}],
  "image_prompts": ["image_post only — 1 prompt"]}}"""


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
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for phrase in (cfg.get("visual_keywords") or []) + [cfg.get("label") or ""]:
            words.update(w for w in re.split(r"\W+", str(phrase).lower()) if len(w) > 2)
    return frozenset(words)


def signal_own_vocabulary(signal: dict) -> frozenset[str]:
    """Words the signal ITSELF also writes in lowercase.

    Harvested signals quote their sources verbatim, and source titles are
    styled: "Did Somebody Order Roots?", "MY AFRICAN VIOLET'S FLOWERS ARE
    SPARKLY". Read as grammar, that styling turns ordinary vocabulary into
    named subjects, and then a plant post is blocked for the word "roots".
    The signal answers this itself — it writes "new root growth" and "zero
    flowers" in the same breath. A word the signal uses lowercase somewhere
    is common vocabulary, whatever a headline did to it elsewhere.

    Singulars and plurals are folded together so "Roots" is answered by
    "root"; a real name is not saved by this, because nobody writes
    "copenhagen" mid-sentence."""
    text = " ".join(str(signal.get(f) or "") for f in ("topic", "summary", "why_now"))
    words = {w for w in re.split(r"[^a-z']+", text) if len(w) > 1}
    return frozenset(words | {w[:-1] for w in words if w.endswith("s")})


def _is_common(word: str, own: frozenset[str]) -> bool:
    w = word.lower()
    return w in own or (w.endswith("s") and w[:-1] in own)


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
    own = signal_own_vocabulary(signal)
    # a run whose every word the signal also writes lowercase is a styled
    # headline, not a name
    return {f for f in found
            if f.lower() not in GENERIC_PROPER
            and not all(_is_common(w, own) for w in f.split())}


@lru_cache(maxsize=8)
def persona_vocabulary(persona_id: str | None) -> frozenset[str]:
    """The words this persona writes into EVERY prompt by configuration.

    The style suffix, the palette, the world list — our own vocabulary, put
    there by us. A word we always write cannot be evidence that a name
    travelled out of a signal, and treating it as one blocks the account for
    saying "wide" in a wide-angle brief."""
    vis = persona.load(persona_id).get("visual_grammar") or {}
    text = " ".join(str(vis.get(k) or "") for k in
                    ("style_suffix", "palette", "avoid", "judge_criteria",
                     "constructible", "shot_scale", "detail_density"))
    text += " " + " ".join(str(x) for x in (vis.get("recurring_world") or []))
    content = persona.load(persona_id).get("content") or {}
    text += " " + " ".join(str(content.get(k) or "") for k in
                           ("slideshow_structure", "slideshow_style_suffix"))
    return frozenset(w for w in re.split(r"[^a-z]+", text.lower()) if len(w) > 2)


def real_subject_leaks(signal: dict, image_prompts: list[str],
                       persona_id: str | None = None) -> list[str]:
    """Named subjects that travelled from the signal into an image prompt.

    Every image we publish is synthetic, so depicting a specific real place,
    venue or brand fabricates a verifiable subject — a lie the AI disclosure
    does not cure, and the fastest route to a reported account. Signals name
    real places constantly; the brief must take the aesthetic and leave the
    proper nouns."""
    joined = " ".join(image_prompts).lower()
    own = signal_own_vocabulary(signal)
    leaks = set()
    # a run like "Copenhagen NYC Boston" is several names in a row — every
    # token is its own real subject, so check them all, not just the first
    for noun in signal_proper_nouns(signal):
        for word in noun.split():
            w = word.lower()
            if len(w) < 3 or w in GENERIC_PROPER or w in category_vocabulary():
                continue
            if _is_common(w, own):      # the signal's own lowercase vocabulary
                continue
            if w in persona_vocabulary(persona_id):   # and our own
                continue
            if re.search(rf"\b{re.escape(w)}\b", joined):
                leaks.add(word)
    return sorted(leaks)


WALL_WORDS = ("wall", "panell", "panel", "wainscot", "plaster", "wallpaper",
              "tile", "splashback", "backsplash")


def tidy_label(label: str) -> str:
    """Put a scheme's label in the shape the boards need — for free.

    This runs BEFORE anything is rendered, because every rule it enforces is
    one that otherwise costs a picture to discover: a board whose first band
    is a tap instead of the wall, a material with no colour to tint it, a
    sixth band nobody can read. Mechanical, so it cannot be argued with, and
    free, so it never competes with the budget for the images themselves.
    """
    from studio import factory

    pairs = factory.parse_spec(label)
    if not pairs:
        return label
    # A LEADING colourless item is a title, not an omission: the style-swap
    # format opens on the name of the style ("Moroccan"), which has no
    # colour of its own and must not be trimmed or shuffled behind a wall.
    title = pairs[:1] if not pairs[0][1] else []
    body = pairs[1:] if title else pairs

    # Elsewhere a material with no colour cannot steer a texture or tint a
    # swatch. Dropping them ALL would leave a board with no names, which is
    # worse than an untinted band — so this only trims a mixed list.
    coloured = [(n, h) for n, h in body if h]
    body = coloured or body
    if title:
        pairs = (title + body)[:4]
    else:
        # THE WALLS FIRST: most of what the eye sees, and the thing the room
        # is judged on afterwards
        walls = [x for x in body if any(w in x[0].lower() for w in WALL_WORDS)]
        rest = [x for x in body if x not in walls]
        pairs = (walls[:1] + rest + walls[1:])[:4]
    return " · ".join(" ".join(part for part in (n, h) if part) for n, h in pairs)


def family_clashes(labels: list[str], style: dict | None) -> list[str]:
    """Two named styles from the same family, found BEFORE anything renders.

    The distance gate already catches a near-duplicate room — but only after
    both have been paid for, and then the reel goes out with four styles
    instead of five. Two styles from one family are the commonest way to get
    there, and reading their names is free.

    A style the list does not know is left alone: the point is to catch
    "Scandinavian AND Japandi", not to make the brain pick from a menu."""
    from studio import factory

    families = (style or {}).get("style_families") or {}
    if not families:
        return []
    seen, clashes = {}, []
    for label in labels:
        name = factory.style_name(label).lower().strip()
        if not name:
            continue
        for family, members in families.items():
            if name not in [str(m).lower() for m in members]:
                continue
            if family in seen:
                clashes.append(f'"{factory.style_name(label)}" and "{seen[family]}" '
                               f'are both {family} — two rooms that will look '
                               f'like one')
            else:
                seen[family] = factory.style_name(label)
            break
    return clashes


def normalise_frame_specs(brief: dict) -> list[str]:
    """On-screen labels, one per frame, in the frames' own order.

    A reply that names fewer decisions than it has frames leaves the tail
    unlabelled rather than shifting labels onto the wrong picture — a spec
    line under the wrong swap is worse than no spec line at all."""
    prompts = brief.get("image_prompts") or []
    specs = [str(x or "").strip() for x in (brief.get("frame_specs") or [])]
    specs = [tidy_label(x) for x in specs[:len(prompts)]]
    return specs + [""] * (len(prompts) - len(specs))


# The three halves of the comparison contract, each overridable by a named
# style. They are separate because a style can need one and not the others,
# and because the lesson from label_rule holds for all of them: a style rule
# APPENDED to a default is a rule the model obeys second. It has to replace.
DEFAULT_BASE_SCENE_RULE = """the room written ONCE — camera position, what is
    in it, where things sit, the light. It must contain NO colours, finishes
    or materials that a frame is going to change, and it is repeated verbatim
    in every frame."""

DEFAULT_CHANGE_RULE = """names ONLY that frame's finishes (wall colour,
    cabinet fronts, worktop, flooring, hardware, textiles) — never
    re-describe the room, the camera or the furniture, and never move
    anything."""

DEFAULT_LABEL_RULE = """names this frame's materials with the SURFACE each
    one covers and its own hex colour code, THE WALLS FIRST — they are most
    of what the eye sees, and a label that omits them explains nothing.
    "limewashed plaster walls #EDE6DA · chocolate velvet headboard #4A3728 ·
    dark walnut floor #4A3728". Every material that can carry a colour
    carries one; only a material with no colour to give (clear glass,
    mirror) may go without. Generic material names and hex codes only,
    never a real paint brand, product or SKU."""


def make_brief(signal: dict, fmt: str, model: str | None = None,
               avoid_captions: list[str] | None = None,
               avoid_subjects: list[str] | None = None,
               voice_problems: list[str] | None = None,
               persona_id: str | None = None,
               style: dict | None = None) -> dict:
    from studio import llm

    # persona voice is the audience-facing text — bump to a stronger model via
    # BRAIN_MODEL in .env if captions ever feel flat (handbook: quality tier
    # only where the audience reads it)
    model = model or os.environ.get("BRAIN_MODEL", llm.DEFAULT_MODEL)

    p = persona.load(persona_id)
    ident, voice, vis = p["identity"], p["voice"], p["visual_grammar"]

    # Style-bible extensions are optional per persona: a persona that has
    # signed the fuller contract (pillars, constructible-dream rules) gets it
    # in the prompt; one that hasn't sees exactly the prompt it always did.
    extra_voice = ""
    if voice.get("pillars"):
        extra_voice += ("\n- PILLARS (the account lives on these): "
                        + "; ".join(str(x) for x in voice["pillars"]))
    if voice.get("example_lines"):
        extra_voice += ("\n- lines in her voice, for rhythm only — NEVER copy "
                        "or lightly reword them: "
                        + " | ".join(f'"{x}"' for x in voice["example_lines"]))
    extra_visual = ""
    if vis.get("constructible"):
        extra_visual += f"\n- constructible dreams only: {str(vis['constructible']).strip()}"
    if fmt == "slideshow_video":
        content_cfg = p.get("content") or {}
        # a named style (config/formats/*.yaml) owns the structure; a persona
        # that has adopted none still describes its own slideshows
        ss = (style or {}).get("structure") or content_cfg.get("slideshow_structure")
        if ss:
            extra_visual += f"\n- SLIDESHOW STRUCTURE (mandatory): {str(ss).strip()}"

        move = content_cfg.get("slideshow_caption_move")
        if move:
            extra_voice += f"\n- CAPTION MOVE for this format: {str(move).strip()}"
    if vis.get("no_transformation_claims"):
        extra_visual += ("\n- no transformation claims: "
                         f"{str(vis['no_transformation_claims']).strip()}")

    # a persona may frame this format differently from its usual post — June's
    # comparison reel is a wide establishing room, the opposite of her
    # intimate default, so the suffix that carries the lens is format-scoped
    suffix_src = vis["style_suffix"]
    if fmt == "slideshow_video":
        suffix_src = ((style or {}).get("style_suffix")
                      or (p.get("content") or {}).get("slideshow_style_suffix")
                      or suffix_src)

    # A named style owns its label completely. Appending its rule alongside
    # the default produced labels that obeyed the default and ignored the
    # style — the style-swap reel came back with no style names on it at all.
    label_rule = str((style or {}).get("label_rule") or DEFAULT_LABEL_RULE).strip()
    base_scene_rule = str((style or {}).get("base_scene_rule")
                          or DEFAULT_BASE_SCENE_RULE).strip()
    change_rule = str((style or {}).get("change_rule")
                      or DEFAULT_CHANGE_RULE).strip()

    # Some styles open on a line of their own, on a frame with no label — the
    # morph reel's first two seconds are the tired room and one sentence over
    # it, and that sentence is the reason anyone watches the rest.
    opening_rule = str((style or {}).get("opening_line_rule") or "").strip()

    prompt = PROMPT.format(
        name=ident["name"], tagline=ident["tagline"], premise=ident["premise"],
        register=voice["register"], rhythm=voice["sentence_rhythm"],
        emoji=voice["emoji_policy"], hashtags=voice["hashtag_policy"],
        never="; ".join(voice["never_says"]),
        instead="; ".join(voice["says_instead"]),
        extra_voice=extra_voice,
        palette=vis["palette"], world="; ".join(vis["recurring_world"]),
        style_suffix=str(suffix_src).strip(), avoid=vis["avoid"],
        label_rule=label_rule, base_scene_rule=base_scene_rule,
        change_rule=change_rule,
        extra_visual=extra_visual,
        sig_topic=signal["topic"], sig_type=signal["signal_type"],
        sig_summary=signal["summary"], sig_why=signal["why_now"],
        fmt=fmt,
    )
    if opening_rule:
        prompt += (f"\n\nALSO RETURN an \"opening_line\" key: {opening_rule}\n"
                   f"It is burned onto the video's first frame at a size you "
                   f"read from across a room, so it must survive being read in "
                   f"under two seconds.")
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
    if persona_id:
        # the operator's own words on what they turned down, so the same
        # miss is not made twice
        try:
            from studio import draftpool
            rejections = draftpool.recent_rejections(persona_id)
        except Exception:
            rejections = []
        try:
            already = draftpool.recent_subjects(persona_id)
        except Exception:
            already = []
        if already:
            listing = "\n".join(f"- {x}" for x in already)
            prompt += (f"\n\nALREADY SHOT — this account's last posts. Do not "
                       f"repeat their SUBJECT: a different room, different "
                       f"objects, a different reason to look. An account that "
                       f"posts the same corner every day reads as one picture "
                       f"on a loop, and the operator turned seven such drafts "
                       f"down in a row:\n{listing}")
        if rejections:
            listing = "\n".join(f'- "{r}"' for r in rejections)
            prompt += (f"\n\nTHE OPERATOR REJECTED RECENT DRAFTS FOR THESE "
                       f"REASONS — do not repeat the same mistake:\n{listing}")
    if signal.get("signal_type") == "operator":
        # not a trend to interpret — the operator is pointing at something
        # that already worked and saying "one like this"
        prompt += ("\n\nTHIS IS NOT A TREND, IT IS THE OPERATOR'S INSTRUCTION. "
                   "Follow it literally and completely: if it names a room, "
                   "that is the room; if it names a structure, that is the "
                   "structure; if it names what changes between frames, that "
                   "is what changes. It outranks every default in this prompt "
                   "about what to depict — the persona's voice, palette "
                   "discipline and safety rules still hold, the subject is "
                   "theirs.")
    if voice_problems:
        listing = "\n".join(f"- {x}" for x in voice_problems)
        prompt += (f"\n\nREJECTED: your last caption broke the voice contract:\n{listing}\n"
                   f"Rewrite the caption. Never command the reader — write the sentence "
                   f"a sharer would send along with the image, and respect the hashtag "
                   f"and emoji limits.")
    reply = llm.complete(prompt, model=model, max_tokens=1500)
    brief = llm.extract_json(reply)
    brief["format"] = fmt
    brief["model"] = model

    # The comparison set is assembled here rather than trusted to the model:
    # asked to "repeat the base scene word for word" it paraphrases, and a
    # paraphrase is a different room. Same string every frame, one clause
    # swapped — the only version of this the renderer can honour.
    swaps = [x for x in (brief.get("frame_swaps") or []) if isinstance(x, dict)]
    if fmt != "image_post" and brief.get("base_scene") and swaps:
        base = str(brief["base_scene"]).strip().rstrip(" .,")
        brief["image_prompts"] = [
            f"{base}, {str(x.get('change') or '').strip().rstrip(' .,')}"
            for x in swaps]
        brief["frame_specs"] = [str(x.get("label") or "").strip() for x in swaps]
        # kept raw: an instruction editor is told what CHANGES, while the
        # composed prompt above describes the whole room and would mostly
        # re-confirm what the editor can already see
        brief["frame_changes"] = [str(x.get("change") or "").strip() for x in swaps]
        # the base scene with nothing swapped into it yet. A morph style opens
        # on the room BEFORE any of the five decisions — the tired version
        # nobody would want — so it needs the scene as its own prompt, not
        # only as the shared half of the five.
        brief["base_prompt"] = base

    # mechanical guards — never trust one layer
    lo, hi = ((style or {}).get("frames") or [4, 6])[:2]
    minimum, cap = (1, 1) if fmt == "image_post" else (int(lo), int(hi))
    brief["image_prompts"] = (brief.get("image_prompts") or [])[:cap]
    if len(brief["image_prompts"]) < minimum:
        raise ValueError(f"brain returned {len(brief['image_prompts'])} prompts, need {minimum}")
    brief["frame_specs"] = normalise_frame_specs(brief)
    suffix = str(suffix_src).strip()
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
