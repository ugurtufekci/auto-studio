"""Which style is working, and what the studio does about it.

The join is the delicate part: the operator publishes by hand, so nothing
carries an id we issued and the caption is the only thread back to the draft
— and most of those drafts never get marked approved, because pressing
Approve would publish a second copy.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio import learning  # noqa: E402


def _ledger(monkeypatch, drafts: list[dict], posts: list[dict], tmp_path: Path):
    from studio import draftpool

    monkeypatch.setattr(draftpool, "pending",
                        lambda: [d for d in drafts if d.get("status") == "pending"])
    monkeypatch.setattr(draftpool, "resolved",
                        lambda *a, **k: [d for d in drafts
                                         if d.get("status") != "pending"])
    acct = tmp_path / "instagram--june"
    acct.mkdir(parents=True)
    (acct / "latest.json").write_text(json.dumps({"posts": posts}), encoding="utf-8")
    return tmp_path


def test_a_hand_published_post_is_matched_to_the_draft_still_in_the_queue(
        monkeypatch, tmp_path):
    drafts = [{"persona": "june", "status": "pending", "id": "d1",
               "text": "five material worlds, same footprint.\n\noxblood marble.",
               "provenance": {"format": "material-board"}}]
    posts = [{"caption": "five material worlds, same footprint.\n\noxblood "
                         "marble.\n\n#jewelbox #materials\n🤖 AI-generated",
              "reach": 159, "saved": 1, "likes": 0}]
    base = _ledger(monkeypatch, drafts, posts, tmp_path)

    rows = learning.attribute("june", "june", base)
    assert len(rows) == 1 and rows[0]["format"] == "material-board"
    # reach 159 + one save at its stated worth
    assert rows[0]["worth"] == 159 + learning.WORTH["saved"]


def test_a_rejected_draft_never_claims_a_post(monkeypatch, tmp_path):
    drafts = [{"persona": "june", "status": "rejected", "id": "d0",
               "text": "quiet corner one", "provenance": {"format": "colourway"}},
              {"persona": "june", "status": "pending", "id": "d1",
               "text": "quiet corner one", "provenance": {"format": "material-board"}}]
    posts = [{"caption": "quiet corner one", "reach": 10}]
    base = _ledger(monkeypatch, drafts, posts, tmp_path)
    assert learning.attribute("june", "june", base)[0]["format"] == "material-board"


def test_an_unmeasured_style_is_shot_before_it_is_judged(monkeypatch, tmp_path):
    drafts = [{"persona": "june", "status": "pending", "id": "d1",
               "text": "one room five ways", "provenance": {"format": "colourway"}}]
    posts = [{"caption": "one room five ways", "reach": 400, "saved": 3}]
    base = _ledger(monkeypatch, drafts, posts, tmp_path)

    pick, why = learning.choose("june", "june", ["colourway", "material-board"], base)
    assert pick == "material-board" and "no measured posts" in why


def test_the_leader_is_favoured_but_the_loser_still_gets_turns(monkeypatch, tmp_path):
    drafts = [{"persona": "june", "status": "pending", "id": "a",
               "text": "winner caption here", "provenance": {"format": "material-board"}},
              {"persona": "june", "status": "pending", "id": "b",
               "text": "loser caption here", "provenance": {"format": "colourway"}}]
    posts = [{"caption": "winner caption here", "reach": 900, "saved": 5},
             {"caption": "loser caption here", "reach": 20, "saved": 0}]
    base = _ledger(monkeypatch, drafts, posts, tmp_path)

    picks = [learning.choose("june", "june", ["material-board", "colourway"],
                             base, random.Random(seed))[0] for seed in range(60)]
    winners = picks.count("material-board")
    assert winners > 40, "the measured leader should take most runs"
    assert picks.count("colourway") >= 5, "a losing style must still be tried"


def test_scores_survive_a_capture_with_no_captions(monkeypatch, tmp_path):
    """Captions were only added to the capture later; an older ledger has
    none, and that must read as 'nothing attributed yet', not a crash."""
    drafts = [{"persona": "june", "status": "pending", "id": "d1",
               "text": "some caption", "provenance": {"format": "colourway"}}]
    base = _ledger(monkeypatch, drafts, [{"reach": 100}], tmp_path)
    assert learning.format_scores("june", "june", base) == {}
