"""Multi-persona tests — the studio speaks as one character at a time.

The invariant worth guarding here is narrow and severe: the disclosure line is
per-persona, so a cycle that resolves the wrong persona publishes one
character's disclosure under another's name. Everything else in this file
exists to keep that from happening quietly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import persona  # noqa: E402

REQUIRED = {
    "identity": ("name", "premise", "disclosure", "post_disclosure", "bio"),
    "profile": ("display_name", "avatar_prompt", "intro_post"),
    "voice": ("register", "never_says", "says_instead"),
    "visual_grammar": ("palette", "recurring_world", "style_suffix", "avoid"),
    "content": ("category", "posts_per_day"),
}


def test_every_persona_config_is_complete():
    ids = persona.available()
    assert ids, "no persona configs found"
    categories = {p.stem for p in (ROOT / "config" / "categories").glob("*.yaml")}
    for pid in ids:
        p = persona.load(pid)
        assert p["id"] == pid
        for section, keys in REQUIRED.items():
            assert section in p, f"{pid}: missing '{section}'"
            for key in keys:
                assert p[section].get(key), f"{pid}: {section}.{key} is empty"
        assert p["content"]["category"] in categories, \
            f"{pid}: category '{p['content']['category']}' has no config"


def test_disclosure_is_per_persona_and_never_leaks_across():
    """The one that matters. disclosure_for() must answer for the persona it
    was asked about — not for whichever one happens to be the default."""
    from studio.publisher import compose_plain, disclosure_for

    for pid in persona.available():
        expected = persona.load(pid)["identity"]["post_disclosure"].strip()
        assert disclosure_for(None, pid) == expected
        assert compose_plain("a caption", 300, None, pid).endswith(expected)


def test_stock_provenance_still_beats_the_persona_line():
    """Provenance outranks persona: a licensed photo is credited, never
    described as AI-generated, whoever is speaking."""
    from studio.publisher import disclosure_for

    stock = {"model": "pexels:Jane Doe", "credit": {"photographer": "Jane Doe"}}
    for pid in persona.available():
        out = disclosure_for(stock, pid)
        assert "Jane Doe" in out and "AI-generated" not in out


def test_personas_do_not_share_a_bio_or_display_name():
    """Identical profile copy across personas is a stronger fleet fingerprint
    than a repeated handle pattern — config/naming.md §2."""
    bios, names = set(), set()
    for pid in persona.available():
        p = persona.load(pid)
        bio = " ".join(p["identity"]["bio"].split())
        name = p["profile"]["display_name"]
        assert bio not in bios, f"{pid}: bio duplicates another persona's"
        assert name not in names, f"{pid}: display name duplicates another persona's"
        bios.add(bio)
        names.add(name)


def test_default_persona_resolution(monkeypatch):
    monkeypatch.setenv("PERSONA", persona.available()[-1])
    assert persona.default_id() == persona.available()[-1]
    monkeypatch.setenv("PERSONA", "nobody-by-that-name")
    with pytest.raises(ValueError, match="no config"):
        persona.default_id()
    monkeypatch.delenv("PERSONA")
    assert persona.default_id() == persona.available()[0]


def test_unknown_persona_names_the_alternatives():
    with pytest.raises(FileNotFoundError) as e:
        persona.load("not-a-persona")
    assert "available" in str(e.value)


def test_registry_personas_all_have_configs():
    """A registry row pointing at a persona with no config would publish
    nothing and measure nothing — catch it here rather than at 04:00."""
    from studio.metrics import fleet_accounts

    ids = set(persona.available())
    for row in fleet_accounts():
        assert row["persona"] in ids, \
            f"accounts.yaml references unknown persona '{row['persona']}'"


def test_registry_does_not_duplicate_the_category():
    """The persona owns its category; a second copy in the registry would be
    free to drift. Rows may be enriched at load time, but the file must not
    carry it."""
    raw = yaml.safe_load((ROOT / "config" / "accounts.yaml").read_text())
    for row in raw["accounts"]:
        assert "category" not in row, \
            f"{row['persona']}/{row['platform']}: category belongs to the persona"
