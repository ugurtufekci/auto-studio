"""The gate that decides whether a scheme is a room or a paint bucket.

The operator's complaint, twice: "mutfak ya komple yesil ya komple turuncu
gibi tek renkten ibaret olmamali". The gate exists to catch that before it
reaches the queue — and, just as importantly, to let a richly coloured room
through, because the same operator asked for "luks etkileyici" next.
"""

import pytest

from studio import factory

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _flooded(path, rgb=(196, 92, 40)):
    """One hue over the whole frame — but with a blown-out window in it, as
    every real render has. The window is what makes this the interesting
    case: it gives the picture a wide lightness range, so the flatness check
    passes it and the saturation check is the one that has to catch it."""
    im = Image.new("RGB", (400, 500))
    for y in range(500):
        k = 0.55 + 0.45 * (1 - y / 500)
        im.paste(tuple(int(c * k) for c in rgb), (0, y, 400, y + 1))
    im.paste((252, 250, 246), (150, 60, 340, 200))      # the window
    im.save(path)
    return str(path)


def _four_materials(path):
    """Pale wall, dark cabinet, mid worktop, warm floor — a real scheme."""
    im = Image.new("RGB", (400, 500))
    for i, c in enumerate([(240, 235, 226), (59, 48, 39),
                           (185, 138, 106), (217, 190, 142)]):
        im.paste(c, (0, i * 125, 400, (i + 1) * 125))
    im.save(path)
    return str(path)


LABEL = ("chalk plaster walls #F0EBE2 · dark ash cabinets #3B3027 · "
         "clay terracotta worktop #B98A6A · pale ash floor #D9BE8E")


def test_one_colour_over_the_whole_room_is_caught(tmp_path):
    path = _flooded(tmp_path / "flood.png")
    lums = [l for _, _, l, _ in factory._surfaces(path)]
    assert max(lums) - min(lums) > 0.5, "the window must widen the range"
    note = factory.monochrome_flood(path, LABEL)
    assert note and "same saturated colour" in note


def test_a_flat_room_is_caught_even_without_saturation(tmp_path):
    """Beige on every surface is the other half of the same complaint, and
    it has no hue to measure — the flatness check is what catches it."""
    im = Image.new("RGB", (400, 500))
    for y in range(500):
        k = 0.92 + 0.08 * (1 - y / 500)
        im.paste(tuple(min(255, int(c * k)) for c in (208, 200, 188)),
                 (0, y, 400, y + 1))
    im.save(tmp_path / "beige.png")
    note = factory.monochrome_flood(str(tmp_path / "beige.png"), LABEL)
    assert note and "one flat colour" in note


def test_four_real_materials_pass(tmp_path):
    assert factory.monochrome_flood(_four_materials(tmp_path / "ok.png"),
                                    LABEL) == ""


def test_a_rich_room_is_not_a_flood(tmp_path):
    """Deep colour is wanted — "luks etkileyici wow dedirten seyler". What
    is rejected is deep colour on EVERY surface, so a saturated room with
    genuinely different hues has to survive."""
    im = Image.new("RGB", (400, 500))
    for i, c in enumerate([(107, 44, 44), (201, 187, 164),
                           (60, 82, 66), (168, 132, 63)]):
        im.paste(c, (0, i * 125, 400, (i + 1) * 125))
    im.save(tmp_path / "rich.png")
    assert factory.monochrome_flood(str(tmp_path / "rich.png"), LABEL) == ""


def test_surfaces_reads_materials_not_positions(tmp_path):
    """The bug this replaces: three horizontal bands were AVERAGED, so a
    wide kitchen whose every band held wall, cabinet, worktop and window
    measured as three middling greys — the richer the room, the flatter it
    scored. Same four materials, scrambled across the frame instead of
    stacked, must read the same."""
    stacked = _four_materials(tmp_path / "stacked.png")
    scrambled = Image.new("RGB", (400, 500))
    cols = [(240, 235, 226), (59, 48, 39), (185, 138, 106), (217, 190, 142)]
    for bx in range(0, 400, 40):          # every material in every band
        for by in range(0, 500, 125):
            scrambled.paste(cols[(bx // 40) % 4], (bx, by, bx + 40, by + 125))
    scrambled.save(tmp_path / "scrambled.png")

    a = {round(l, 1) for _, _, l, _ in factory._surfaces(stacked)}
    b = {round(l, 1) for _, _, l, _ in
         factory._surfaces(str(tmp_path / "scrambled.png"))}
    assert a == b, (a, b)
    assert factory.monochrome_flood(str(tmp_path / "scrambled.png"),
                                    LABEL) == ""


def test_a_change_confined_to_part_of_the_frame_still_counts(tmp_path):
    """The bug this pins: frame_distance averaged the whole picture, so a
    wide room whose lake, mountains and ceiling cannot change diluted every
    real repaint toward zero. Repainting the cabinets of a big kitchen
    measured 7.8 against a floor of 18 and was thrown away — twice, on two
    different runs, costing both of them."""
    def room(path, floor):
        im = Image.new("RGB", (400, 500), (208, 202, 190))   # walls, ceiling
        im.paste((150, 190, 210), (40, 60, 360, 240))        # the view out
        im.paste(floor, (0, 380, 400, 500))                  # the floor
        im.save(path)
        return str(path)

    base = room(tmp_path / "a.png", (196, 178, 150))
    repainted = room(tmp_path / "b.png", (96, 58, 40))     # floor only
    untouched = room(tmp_path / "c.png", (198, 180, 152))  # nothing real

    assert factory.frame_distance(base, repainted) >= factory.SCHEME_MIN_DISTANCE
    assert factory.frame_distance(base, untouched) < factory.SCHEME_MIN_DISTANCE

    # and the same pair judged by the mean would have failed the repaint
    from PIL import ImageChops, ImageStat
    ims = [Image.open(p).convert("RGB").resize((64, 64))
           for p in (base, repainted)]
    mean = ImageStat.Stat(ImageChops.difference(*ims)).mean[0]
    assert mean < factory.frame_distance(base, repainted)


def test_rejudge_matches_a_fresh_render_scheme_for_scheme(tmp_path):
    """Frames reused from an earlier run go through the same gate in the
    same cumulative order — that is the whole point of being able to re-run
    a cycle when the GATE was what was wrong."""
    paths = []
    for i, c in enumerate([(210, 200, 185), (212, 202, 187), (90, 120, 80)]):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (400, 500), c).save(p)
        paths.append(str(p))

    out = factory.rejudge(paths[0], paths[1:], ["", ""])
    assert len(out) == 2
    assert "still looks like the base room" in out[0]["mismatch"]
    assert not out[1].get("mismatch")
    assert all(o["model"] == "reused" for o in out)
