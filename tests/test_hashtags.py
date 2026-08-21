"""Hashtags must survive the platforms' own parsing rules.

Every platform ends a hashtag at the first character outside letters,
digits and underscore. The persona brain sometimes writes a multi-word tag
with a separator — the queue held #kitchen-materials, #tavern-light,
#autumn_light and friends on 2026-08-21 — and a hyphenated tag then links
as its first word with the rest left as dead text: "#kitchen" +
"-materials". The operator saw exactly that pasting a caption for a
hand-post and asked whether it was deliberate. It was not: nothing between
the brain and the caption enforced the rule. Now compose does, so every
adapter, the console preview and the copy-for-hand-posting path all emit
tags a platform links whole.
"""

from studio.publisher import _trim_hashtags, compose_plain, normalise_hashtags


def test_a_hyphenated_tag_collapses_to_one_linkable_word():
    assert normalise_hashtags("warm floors #kitchen-materials #tavern-light") \
        == "warm floors #kitchenmaterials #tavernlight"


def test_an_underscored_tag_collapses_to_the_house_style():
    """Underscores do stay clickable on Instagram, but every deliberate tag
    in the voice is words run together — mixed forms read as two different
    authors."""
    assert normalise_hashtags("#autumn_light #terrace_room autumn") \
        == "#autumnlight #terraceroom autumn"


def test_prose_around_the_tags_is_untouched():
    """A hyphen in prose, an em-dash, a full stop after a trailing tag —
    none of these belong to a tag and none may move."""
    text = "sun-lit corners — the room breathes. #interiors."
    assert normalise_hashtags(text) == text


def test_a_collapse_that_creates_a_duplicate_drops_the_repeat():
    """#stained-glass beside #stainedglass would collapse into the same tag
    twice, which reads bot-like in a caption."""
    assert normalise_hashtags("glow #stained-glass #stainedglass #daylight") \
        == "glow #stainedglass #daylight"


def test_the_cap_counts_a_hyphenated_tag_as_one_tag():
    """Before normalisation ran first, the trimmer's #\\w+ saw
    "#kitchen-materials" as the tag "#kitchen" — trimming it left
    "-materials" behind as garbage."""
    text = "room #one-two #three #four"
    assert _trim_hashtags(normalise_hashtags(text), 1) == "room #onetwo"


def test_compose_emits_only_whole_linkable_tags(monkeypatch):
    """End to end through the shared compose path: what publishes, what the
    console previews and what the operator copies for a hand-post carry no
    separator inside any tag."""
    import studio.publisher as pub
    monkeypatch.setattr(pub, "disclosure_for", lambda p, i: "🤖 AI")
    out = compose_plain("evening glow #amber-light #autumn_light",
                        300, None, None, max_hashtags=5)
    assert "#amberlight #autumnlight" in out
    assert "-" not in out.split("#", 1)[1]
