"""The style bible, enforced — captions and provenance held to the persona.

The bible itself lives in the persona file (config/personas/*.yaml): §voice
carries the pillars and the banned phrases, §visual_grammar carries the world
and the judge's criteria, and `style_version` names the whole identity so a
deliberate change is a version bump with git history, never drift.

This module is the mechanical layer — the parts that must not depend on a
model behaving:

  caption_problems()  refuses engagement bait, hashtag walls and emoji spam.
                      A share is an emotional act; the caption's job is to
                      gift the sharer their sentence, never to command the
                      reader. The banned list is config, so each persona
                      draws its own line.
  style_version()     the version string stamped into every post's
                      provenance — which identity produced this asset.

A persona without `banned_phrases` (Mara today) gets no caption opinions at
all: the linter only enforces contracts a persona has actually signed.
"""

from __future__ import annotations

from studio import persona

# Symbol ranges that read as emoji in a caption. Deliberately narrow —
# a false accusation costs a regeneration, a miss costs one emoji.
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))
_DISCLOSURE_ROBOT = 0x1F916  # 🤖 — appended mechanically, never June's choice


def _emoji_count(text: str) -> int:
    return sum(1 for ch in text
               if any(a <= ord(ch) <= b for a, b in _EMOJI_RANGES)
               and ord(ch) != _DISCLOSURE_ROBOT)


def style_version(persona_id: str | None = None) -> str:
    """The identity stamp for provenance; empty for personas without one."""
    try:
        return str(persona.load(persona_id).get("style_version") or "")
    except Exception:
        return ""


def caption_problems(caption: str, persona_id: str | None = None) -> list[str]:
    """Everything about this caption the persona's voice contract forbids.

    Empty list = publishable. Problems are worded for the brain's retry
    prompt, so each names the rule broken and the offending fragment."""
    try:
        voice = persona.load(persona_id).get("voice") or {}
    except Exception:
        return []
    problems = []
    low = caption.lower()

    for phrase in voice.get("banned_phrases") or []:
        p = str(phrase).lower().strip().strip('"')
        if p and p in low:
            problems.append(
                f'bait phrase "{p}" — commands the reader instead of gifting '
                f"the sharer a sentence")

    hashtag_max = voice.get("hashtag_max")
    if hashtag_max is not None:
        tags = low.count("#")
        if tags > int(hashtag_max):
            problems.append(
                f"{tags} hashtags where the voice allows at most {hashtag_max}")

    emoji_max = voice.get("emoji_max")
    if emoji_max is not None:
        n = _emoji_count(caption)
        if n > int(emoji_max):
            problems.append(
                f"{n} emoji where the voice allows at most {emoji_max} "
                f"(the mechanical 🤖 disclosure not counted)")

    return problems


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    demo = sys.argv[1] if len(sys.argv) > 1 else (
        "Tag someone you'd read here! 😍🌧️✨ #cozy #home #interior #design #nook")
    print(f"style: {style_version() or '(none)'}")
    for problem in caption_problems(demo) or ["caption passes the voice contract"]:
        print(f"  - {problem}")
