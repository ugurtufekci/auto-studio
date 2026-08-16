"""Spec labels on comparison slideshows.

The labels are the format's product — "sage green #9CAF88" on the frame is
what makes a swap readable — so the things worth pinning down are that a
label actually changes the picture, that one type size covers the whole set,
and that an unlabelled persona's slideshow is left alone.
"""

from pathlib import Path

import pytest

from studio import brain, factory

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _still(path: Path, colour=(200, 190, 175)) -> str:
    Image.new("RGB", (1024, 1024), colour).save(path)
    return str(path)


def test_burn_label_changes_the_picture(tmp_path):
    src = _still(tmp_path / "src.png")
    out = factory.burn_label(src, "2 · sage green #9CAF88", tmp_path / "out.png")
    before, after = Image.open(src), Image.open(out)
    assert after.size == (factory.FRAME, factory.FRAME)
    # the label lives in the lower-left, so that corner must have changed
    box = (0, factory.FRAME - 200, 500, factory.FRAME)
    assert list(after.crop(box).tobytes()) != list(
        before.resize((factory.FRAME, factory.FRAME)).crop(box).tobytes())


def test_one_type_size_for_the_whole_set():
    short = ["1 · walnut #5C4033", "2 · sage #9CAF88"]
    long_one = short + ["3 · " + "warm unlacquered brass and cream " * 2]
    assert factory.label_size(short) > factory.label_size(long_one)
    assert factory.label_size(long_one) >= 22


def test_labels_are_padded_and_capped_to_the_frames():
    brief = {"image_prompts": ["a", "b", "c", "d"],
             "frame_specs": ["one", "two"]}
    out = brain.normalise_frame_specs(brief)
    assert out == ["one", "two", "", ""]

    brief = {"image_prompts": ["a", "b"], "frame_specs": ["1", "2", "3", "4"]}
    assert brain.normalise_frame_specs(brief) == ["1", "2"]


def test_unlabelled_slideshow_keeps_its_drift(tmp_path):
    """Mara's slideshows are atmosphere — no labels, no static camera."""
    paths = [_still(tmp_path / f"{i}.png") for i in range(2)]
    cmd = factory.slideshow_command(paths, None, tmp_path, 3.5, [])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "zoom+" in graph and "duration=0.6" in graph

    cmd = factory.slideshow_command(paths, None, tmp_path, 3.5,
                                    ["1 · walnut #5C4033", "2 · sage #9CAF88"])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "zoom+" not in graph and "duration=0.25" in graph


def test_board_prompt_orders_bands_and_burn_changes_each_band(tmp_path):
    """The board format: materials as full-frame horizontal bands, names
    burned at each band's known position — no vision call, no guessing."""
    pairs = [("chocolate velvet", "#4A3728"), ("dark walnut", "")]
    prompt = factory.board_prompt(pairs)
    assert "band 1 — chocolate velvet" in prompt and "band 2 — dark walnut" in prompt
    assert "no text" in prompt

    src = _still(tmp_path / "b.png")
    out = factory.burn_band_names(src, "1 · chocolate velvet #4A3728 · dark walnut",
                                  tmp_path / "named.png", factory.VERTICAL)
    before = Image.open(src).resize(factory.VERTICAL)
    after = Image.open(out)
    H = factory.VERTICAL[1]
    for band in range(2):   # a name landed on BOTH bands
        box = (0, band * H // 2, factory.VERTICAL[0], (band + 1) * H // 2)
        assert after.crop(box).tobytes() != before.crop(box).tobytes()


def test_per_frame_durations_set_cumulative_offsets(tmp_path):
    """Board frames hold shorter than room frames; the xfade offsets must be
    the running sum of the real durations, not multiples of one number."""
    paths = [_still(tmp_path / f"{i}.png") for i in range(4)]
    cmd = factory.slideshow_command(paths, None, tmp_path, 1.8, [],
                                    cut="hard", durations=[1.1, 1.8, 1.1, 1.8])
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "offset=1.10" in graph and "offset=2.90" in graph and "offset=4.00" in graph
    assert "zoom+" not in graph          # hard cut means a locked camera too
