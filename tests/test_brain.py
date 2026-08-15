"""Production-rule tests — the content constraints that keep accounts alive.

Two rules decide whether synthetic media is publishable, and both are easy to
lose silently when a prompt is edited:

  · imagery depicts a KIND of thing, never a specific verifiable one
  · each post carries its own editorial angle, not a template variant

The first is mechanically checkable, so it is checked here against the real
signal pools committed in this repository — the exact text the brain is fed
in production.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.brain import (  # noqa: E402
    PROMPT,
    real_subject_leaks,
    signal_proper_nouns,
)

SIGNAL = {
    "topic": "Alpine lake sunrises",
    "signal_type": "aesthetic",
    "summary": "Granite basins and still water at dawn across the US West.",
    "why_now": "Seven posts in 24 hours from Glacier NP, Mount Fuji, "
               "Doubtful Sound in NZ, plus Copenhagen and Boston skylines.",
}


def test_named_places_are_detected():
    nouns = {n.lower() for n in " ".join(signal_proper_nouns(SIGNAL)).split()}
    assert {"glacier", "fuji", "doubtful", "copenhagen", "boston"} <= nouns


def test_our_own_category_words_are_never_real_subjects():
    """Source titles arrive in title case ("Nature Shapes Every Room"), so
    ordinary category vocabulary looks like a name. It must not be treated as
    one, or every interiors cycle would fail on the word 'room'."""
    sig = {"topic": "Plant-filled interiors",
           "summary": "Green corners everywhere.",
           "why_now": "House tours 'Nature Shapes Every Room' and 'Home Around a Garden'."}
    assert real_subject_leaks(sig, ["a plant-filled living room in window light"]) == []


def test_a_real_subject_in_an_image_prompt_is_a_leak():
    assert real_subject_leaks(SIGNAL, ["Mount Fuji at dawn, mist below"]) == ["Fuji", "Mount"]
    # mid-run names must be caught too, not just the first of a list
    assert real_subject_leaks(SIGNAL, ["Boston rooftops at blue hour"]) == ["Boston"]


def test_generic_imagery_passes():
    for prompt in ("a granite alpine lake at sunrise, mist on still water",
                   "a Western US mountain basin at dawn",
                   "a sunlit corner cafe with big windows and plants"):
        assert real_subject_leaks(SIGNAL, [prompt]) == [], prompt


def test_sentence_initial_capitals_are_not_proper_nouns():
    """Regression: topics start with a capital ("Cozy summer nooks"), which
    must not make every ordinary word a banned subject."""
    sig = {"topic": "Cozy summer nooks", "summary": "Warm corners.",
           "why_now": "Ten posts today."}
    assert real_subject_leaks(sig, ["a cozy warm corner nook in summer light"]) == []


def test_every_committed_signal_pool_stays_checkable():
    """The detector runs against live pools twice a day — a pool that makes it
    throw would break every cycle, so exercise the real data."""
    pools = sorted((ROOT / "data" / "signals").glob("*/latest.json"))
    assert pools, "no signal pools committed"
    for path in pools:
        for sig in json.loads(path.read_text()).get("signals") or []:
            sig = {**sig, "signal_type": sig.get("type", "")}
            assert isinstance(signal_proper_nouns(sig), set)
            # a wholly generic prompt must never trip on any real signal
            assert real_subject_leaks(sig, ["a quiet room in warm morning light"]) == []


def test_production_rules_are_actually_in_the_brief_prompt():
    """These rules live in the prompt the brain is given. If the prompt is
    rewritten without them, the mechanical check still guards imagery but the
    editorial-angle rule has no enforcement at all — so pin both."""
    assert "GENERIC SUBJECTS, NEVER REAL ONES" in PROMPT
    assert "ONE POST, ONE IDEA OF ITS OWN" in PROMPT


def test_quoted_source_titles_do_not_become_named_subjects():
    """A harvested signal quotes its sources verbatim, and source titles are
    styled — "Did Somebody Order Roots?", "MY ... FLOWERS ARE SPARKLY". Read
    as grammar that styling turns ordinary vocabulary into named subjects,
    and a plant post is then blocked for the word "roots"."""
    signal = {
        "topic": "Houseplant blooms & propagation",
        "summary": ("Houseplant owners are posting surprise blooms and new "
                    "root growth — begonias, African violets, shamrocks."),
        "why_now": ("Six r/houseplants posts within 27h: 'Did Somebody Order "
                    "Roots?' (0.6h), 'MY AFRICAN VIOLET'S FLOWERS ARE SPARKLY' "
                    "(11.4h), 'My purple shamrock had zero flowers yesterday'."),
    }
    prompts = ["A windowsill with a flowering houseplant, new roots visible "
               "in a glass jar, violet blooms, warm daylight"]
    assert real_subject_leaks(signal, prompts) == []


def test_the_guard_still_bites_on_a_real_place():
    signal = {"topic": "Nordic cafés",
              "summary": "Writers keep citing Copenhagen and the Blue Bottle fit-out.",
              "why_now": "three features this week"}
    assert real_subject_leaks(
        signal, ["a corner café in Copenhagen, warm light"]) == ["Copenhagen"]
    assert real_subject_leaks(
        signal, ["a sunlit corner café, warm light"]) == []
