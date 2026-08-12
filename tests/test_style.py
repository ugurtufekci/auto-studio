"""Style-bible tests — the voice contract enforced mechanically.

The account's share mechanic is that captions gift the sharer a sentence
instead of commanding the reader. A model can be told this; only a linter
guarantees it. These pin the linter, the judge's style criteria, and that a
persona which never signed the fuller contract (Mara) is left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import style  # noqa: E402

CLEAN = ("breakfast takes forty minutes here. nobody checks their phone.\n\n"
         "#interiors #slowliving #cozyhome")


def test_a_caption_in_junes_voice_passes():
    assert style.caption_problems(CLEAN, "june") == []


def test_engagement_bait_is_refused_with_the_phrase_named():
    problems = style.caption_problems(
        "Tag someone you'd have breakfast with here! #interiors", "june")
    assert problems and 'tag someone' in problems[0]


def test_a_hashtag_wall_is_refused():
    caption = "quiet corner. #a #b #c #d #e #f"
    problems = style.caption_problems(caption, "june")
    assert any("hashtag" in p for p in problems)


def test_emoji_spam_is_refused_but_the_disclosure_robot_is_exempt():
    # 🤖 is appended mechanically by the publisher — never June's own choice,
    # so it must not count against her one-emoji allowance
    assert style.caption_problems("rain again 🌧️ 🤖 #interiors", "june") == []
    problems = style.caption_problems("rain 🌧️✨😍 #interiors", "june")
    assert any("emoji" in p for p in problems)


def test_the_bans_are_case_insensitive():
    problems = style.caption_problems("SAVE THIS for later!!", "june")
    assert problems and "save this" in problems[0]


def test_a_persona_without_the_contract_gets_no_opinions():
    """Mara never signed banned_phrases/hashtag_max — the linter must not
    invent standards for her."""
    wall = "Tag someone! 😍😍😍 #a #b #c #d #e #f #g"
    assert style.caption_problems(wall, "mara") == []


def test_style_version_stamps_june_and_not_mara():
    assert style.style_version("june") == "june-v1"
    assert style.style_version("mara") == ""


def test_junes_judge_criteria_reach_the_judge_prompt(monkeypatch):
    """The judge must grade against the style contract, not generic beauty —
    a wrong standard only ever shows up in the reason string, silently."""
    from studio import factory, llm

    seen = {}

    def fake_complete(prompt, model=None, images=None, max_tokens=0):
        seen["prompt"] = prompt
        return '{"pick": 0, "reason": "on palette"}'

    monkeypatch.setattr(llm, "complete", fake_complete)
    factory.judge_pick([{"path": "x.jpg"}], "a quiet corner", persona_id="june")
    assert "style contract" in seen["prompt"]
    assert "terracotta" in seen["prompt"]


def test_voice_pillars_and_dream_rules_reach_the_brain_prompt(monkeypatch):
    from studio import brain, llm

    seen = {}

    def fake_complete(prompt, model=None, max_tokens=0, images=None):
        seen["prompt"] = prompt
        return ('{"premise":"p","angle":"a","mood":"m","caption":"c",'
                '"alt_text":"alt","voiceover_script":"",'
                '"image_prompts":["a corner"]}')

    monkeypatch.setattr(llm, "complete", fake_complete)
    brain.make_brief({"topic": "t", "signal_type": "topic", "summary": "s",
                      "why_now": "w"}, "image_post", persona_id="june")
    p = seen["prompt"]
    assert "PILLARS" in p and "never command the reader" in p
    assert "constructible" in p and "no transformation claims" in p


def test_voice_rejection_reaches_the_retry_prompt(monkeypatch):
    from studio import brain, llm

    seen = {}

    def fake_complete(prompt, model=None, max_tokens=0, images=None):
        seen["prompt"] = prompt
        return ('{"premise":"p","angle":"a","mood":"m","caption":"c",'
                '"alt_text":"alt","voiceover_script":"",'
                '"image_prompts":["a corner"]}')

    monkeypatch.setattr(llm, "complete", fake_complete)
    brain.make_brief({"topic": "t", "signal_type": "topic", "summary": "s",
                      "why_now": "w"}, "image_post", persona_id="june",
                     voice_problems=['bait phrase "tag someone"'])
    assert "broke the voice contract" in seen["prompt"]
    assert "tag someone" in seen["prompt"]
