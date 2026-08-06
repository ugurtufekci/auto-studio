"""Offline tests — no network, no secrets. These are what CI can actually prove.

They guard the contracts that break silently: config shape, the disclosure gate,
and the guardrail arithmetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config"


def test_persona_config_parses_and_has_disclosure():
    p = yaml.safe_load((CONFIG / "persona.yaml").read_text())
    assert p["identity"]["name"]
    # the disclosure line is the one invariant that must always exist
    assert p["identity"]["post_disclosure"].strip()
    assert p["voice"]["never_says"]
    assert p["visual_grammar"]["style_suffix"].strip()


def test_every_platform_policy_has_required_keys():
    policy = yaml.safe_load((CONFIG / "platform_policy.yaml").read_text())
    for name, cfg in policy.items():
        if name == "fleet":
            continue
        for key in ("hard_max_posts_per_day", "min_gap_hours", "warmup"):
            assert key in cfg, f"{name} missing {key}"
        assert cfg["warmup"], f"{name} has an empty warm-up curve"
        # the curve must terminate in an always-matching stage
        assert cfg["warmup"][-1]["days"] >= 9999, f"{name} warm-up never terminates"


def test_platform_formats_declare_fields_and_limits():
    fmts = yaml.safe_load((CONFIG / "platform_formats.yaml").read_text())
    for name, f in fmts.items():
        assert f["fields"], f"{name} declares no fields"
        if "title" in f["fields"]:
            assert f["title_limit"] and f["description_limit"]
        else:
            assert f["text_limit"], f"{name} has no text_limit"


def test_policy_and_formats_cover_the_same_platforms():
    policy = {k for k in yaml.safe_load((CONFIG / "platform_policy.yaml").read_text())
              if k != "fleet"}
    fmts = set(yaml.safe_load((CONFIG / "platform_formats.yaml").read_text()))
    # a platform we can publish to must have BOTH a safety policy and a format
    missing_format = policy - fmts
    assert not missing_format, f"policy without format: {missing_format}"


def test_disclosure_follows_provenance():
    """A stock photo must never be described as AI-generated."""
    from studio.publisher import disclosure_for

    generated = disclosure_for({"model": "fal-ai/z-image/turbo"})
    stock = disclosure_for({"model": "pexels:Jane Doe",
                            "credit": {"photographer": "Jane Doe"}})
    assert "AI-generated" in generated
    assert "Jane Doe" in stock and "Pexels" in stock
    assert "AI-generated" not in stock


def test_compose_never_truncates_the_disclosure():
    from studio.publisher import compose_plain, disclosure_for

    disclosure = disclosure_for(None)
    long_caption = "x" * 5000
    out = compose_plain(long_caption, limit=300)
    assert out.endswith(disclosure)
    assert len(out) <= 300


@pytest.mark.parametrize("age_days,expected", [(0.5, 0), (3, 1), (30, 2)])
def test_bluesky_warmup_curve(age_days, expected):
    from studio.guard import load_policy, warmup_cap

    assert warmup_cap(load_policy("bluesky"), age_days) == expected


def test_telegram_has_no_warmup_silence():
    """Telegram bots are sanctioned automation — a silent period would be
    protecting against a risk that does not exist there."""
    from studio.guard import load_policy, warmup_cap

    assert warmup_cap(load_policy("telegram"), 0.0) > 0
