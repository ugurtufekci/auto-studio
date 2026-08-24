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
             "frame_specs": ["olive walls #6B8E23", "navy walls #1B3B6F"]}
    out = brain.normalise_frame_specs(brief)
    assert out == ["olive walls #6B8E23", "navy walls #1B3B6F", "", ""]

    brief = {"image_prompts": ["a", "b"],
             "frame_specs": ["a walls #111111", "b walls #222222",
                             "c walls #333333", "d walls #444444"]}
    assert brain.normalise_frame_specs(brief) == ["a walls #111111",
                                                  "b walls #222222"]


def test_a_label_is_put_in_board_order_before_anything_is_rendered():
    """Free, mechanical, and ahead of the spend: the walls lead because they
    are most of what the eye sees, colourless materials are trimmed, and a
    sixth band nobody can read is dropped."""
    tidied = brain.tidy_label(
        "brushed brass taps #D4AF37 · deep ink blue panelled walls #1B3B6F · "
        "pale limestone floor #E8DCC4 · cream runner #F5E6D3 · a fifth #123456")
    assert tidied.startswith("deep ink blue panelled walls #1B3B6F")
    assert tidied.count("·") == 3          # four materials, no more

    # a label with no colours at all keeps its names rather than emptying
    assert brain.tidy_label("plaster walls · oak floor") == "plaster walls · oak floor"


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


def test_a_scheme_that_did_not_change_is_caught(tmp_path):
    """The failure a viewer sees first: the fifth room is the first room
    again because its edit silently did nothing. Measured on a real run, the
    schemes that genuinely changed sat 32-66 apart and that one sat at 10.9."""
    base = _still(tmp_path / "base.png", (30, 45, 80))       # ink blue
    twin = _still(tmp_path / "twin.png", (32, 47, 82))       # the same room
    other = _still(tmp_path / "other.png", (200, 120, 70))   # terracotta

    assert factory.frame_distance(base, twin) < factory.SCHEME_MIN_DISTANCE
    assert factory.frame_distance(base, other) > factory.SCHEME_MIN_DISTANCE


def test_a_style_name_leads_its_label_and_is_never_trimmed():
    """The style-swap format opens on the name of the style, which has no
    colour of its own — trimming it as "colourless" or shuffling a wall in
    front of it would remove the whole point of the frame."""
    out = brain.tidy_label("Moroccan · carved plaster walls #D9C7A7 · "
                           "zellige tile #1F6F5C · aged brass #B5A642")
    assert out.startswith("Moroccan ·")
    assert "carved plaster walls #D9C7A7" in out

    # without a title the walls still lead
    out = brain.tidy_label("aged brass taps #B5A642 · plaster walls #D9C7A7")
    assert out.startswith("plaster walls #D9C7A7")


def test_long_material_names_stay_inside_the_frame(tmp_path):
    """The bug this pins: "moss green chalky limewash walls" ran off the right
    edge of a real carousel slide and shipped as "MOSS GREEN CHALKY LIMEWA".

    Specificity is what this format sells, so the names are long by design and
    the type has to bend to them. Checked by pixels, not by geometry: nothing
    white may be drawn in the right margin, at any canvas."""
    long = ("moss green chalky limewash walls #4C5B3C · "
            "pale honed marble desktop #DCD6CC · "
            "brushed nickel hardware and fittings #B6B8B5")
    src = _still(tmp_path / "long.png", (30, 30, 30))
    for canvas in (factory.CAROUSEL, factory.VERTICAL, factory.SQUARE):
        out = factory.burn_spec_card(src, long, tmp_path / "card.png", canvas)
        img = Image.open(out).convert("RGB")
        W, H = canvas
        margin = img.crop((W - int(W * 0.03), 0, W, H))
        assert max(px[0] for px in margin.getdata()) < 200, (
            f"type reaches the right edge at {canvas}")


def test_wrap_never_drops_the_last_word(tmp_path):
    font = factory._bold_font(40)
    if font is None:
        pytest.skip("no truetype face on this box")
    lines = factory.wrap_to("moss green chalky limewash walls", font, 120)
    assert " ".join(lines).split() == "moss green chalky limewash walls".split()
    assert len(lines) <= 2


def test_board_hook_flashes_the_rooms_and_the_twin_still_gets_rooms():
    """The bug this pins: the hook was gated on a variable only the morph
    branch fills, so `hook: flash` sat in material-board.yaml and never once
    fired. And picking the rooms after the prepend reads boards — the
    carousel twin would ship texture close-ups instead of finished rooms."""
    frames = ["b0", "r0", "b1", "r1", "b2", "r2"]
    order, durations, rooms = factory.board_running_order(frames, 1.1, 1.8, 0.28)

    assert rooms == ["r0", "r1", "r2"], "the twin must get rooms, not boards"
    assert order[:3] == ["r0", "r1", "r2"], "the payoff comes first"
    assert order[3:] == frames, "the sequence itself is untouched"
    assert len(durations) == len(order)
    assert durations[:3] == [0.28] * 3
    assert durations[3:] == [1.1, 1.8] * 3
    assert abs(sum(durations) - (0.84 + 8.7)) < 1e-6


def test_board_hook_off_leaves_the_sequence_alone():
    frames = ["b0", "r0", "b1", "r1"]
    order, durations, rooms = factory.board_running_order(frames, 1.1, 1.8, 0.0)
    assert order == frames and rooms == ["r0", "r1"]
    assert durations == [1.1, 1.8, 1.1, 1.8]

    # one room is not a flash, it is the same picture twice
    one, _, _ = factory.board_running_order(["b0", "r0"], 1.1, 1.8, 0.28)
    assert one == ["b0", "r0"]


def test_refit_bands_keeps_every_band_at_a_new_aspect(tmp_path):
    """A 9:16 board of four equal bands centre-cropped to 4:5 keeps 195px of
    the outer two against 480px of the inner two — the first and last
    material come out as slivers too thin to carry their name plate. Re-cut
    band for band and every material gets the same height."""
    src = tmp_path / "board.png"
    colours = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (220, 220, 40)]
    im = Image.new("RGB", (1080, 1920))
    for i, c in enumerate(colours):
        im.paste(Image.new("RGB", (1080, 480), c), (0, i * 480))
    im.save(src)

    out = Image.open(factory.refit_bands(str(src), 4, tmp_path / "c.png",
                                         factory.CAROUSEL))
    assert out.size == factory.CAROUSEL
    H = factory.CAROUSEL[1]
    for i, c in enumerate(colours):        # each band, still at its own row
        assert out.getpixel((540, int((i + 0.5) * H / 4))) == c

    # every row is now the same height; the crop it replaces was lopsided
    rows = [sum(1 for y in range(H) if out.getpixel((540, y)) == c)
            for c in colours]
    assert max(rows) - min(rows) <= 2, rows

    from PIL import ImageOps
    naive = ImageOps.fit(Image.open(src), factory.CAROUSEL)
    thin = [sum(1 for y in range(H) if naive.getpixel((540, y)) == c)
            for c in colours]
    assert thin == [195, 480, 480, 195], thin      # outer two, less than half
    assert min(thin) < max(thin) / 2


def test_no_persona_room_is_framed_through_a_doorway():
    """The operator's words after the library nook: "dar acidan kapidan
    gozuken bu goruntu icimi daralttI artIk". A doorway frame crops the room
    to a slice, and a slice holds too little for a material swap to read."""
    vis = brain.persona.load("june").get("visual_grammar") or {}
    rooms = vis.get("rooms") or []
    assert rooms, "june must still declare a room pool"
    for room in rooms:
        low = str(room).lower()
        assert "doorway" not in low, room
        assert not low.startswith("a corridor"), room
    assert vis.get("views"), "a wide room needs something beyond the glass"
    for key in ("shot_scale", "style_suffix"):
        assert "from the doorway" not in " ".join(str(vis.get(key, "")).split())


def test_palette_board_paints_the_promised_colours(tmp_path):
    pairs = [("aubergine limewash", "#4A2C46"), ("cream plaster", "#E7DFD2"),
             ("clear glass", "")]
    out = Image.open(factory.palette_board(pairs, tmp_path / "p.png",
                                           factory.CAROUSEL))
    W, H = factory.CAROUSEL
    assert out.getpixel((W // 2, H // 6)) == (0x4A, 0x2C, 0x46)
    assert out.getpixel((W // 2, H // 2)) == (0xE7, 0xDF, 0xD2)
    assert out.getpixel((W // 2, 5 * H // 6)) == factory.PALETTE_NO_HEX


def test_paired_slides_never_cuts_a_pair_in_half():
    """A carousel trimmed with [:cap] can end on a materials sheet that
    introduces nothing. Pairs go in whole or not at all."""
    pairs = [(f"m{i}", f"r{i}") for i in range(6)]        # 12 slides worth
    slides = factory.paired_slides(pairs, cap=10)
    assert len(slides) == 10
    assert slides == ["m0","r0","m1","r1","m2","r2","m3","r3","m4","r4"]

    with_anchor = factory.paired_slides(pairs, cap=10, anchor="before")
    assert len(with_anchor) == 9          # anchor + 4 whole pairs, not 10
    assert with_anchor[0] == "before" and with_anchor[-1] == "r3"

    bare = factory.paired_slides([(None, "r0"), ("m1", "r1")], cap=10)
    assert bare == ["r0", "m1", "r1"]     # an unpaired image rides alone


def test_materials_slide_falls_back_to_flat_and_keeps_the_names(tmp_path, monkeypatch):
    """With no renderer the slide still ships: flat bands in the spec's own
    colours, names on the bands, style title centred when the spec leads
    with one."""
    def refuse(*a, **k):
        raise RuntimeError("no renderer in tests")
    monkeypatch.setattr(factory, "generate_images", refuse)

    out = factory.materials_slide(
        "Art Deco · fluted walnut #6B4A2F · emerald velvet #0E5C4A",
        tmp_path / "card", factory.CAROUSEL)
    img = Image.open(out).convert("RGB")
    assert img.size == factory.CAROUSEL
    W, H = factory.CAROUSEL
    # two texture bands in the promised colours (sampled off-centre, clear
    # of the name plates and the title)
    assert img.getpixel((W - 60, H // 4)) == (0x6B, 0x4A, 0x2F)
    assert img.getpixel((W - 60, 3 * H // 4)) == (0x0E, 0x5C, 0x4A)
    # and something was drawn near the top for the style title
    assert factory.materials_slide("", tmp_path / "none", factory.CAROUSEL) is None


def test_the_sheet_claims_the_rooms_dominant_colour(tmp_path):
    """The live failure this pins (post DcRnitujdJq, 2026-08-20): a Gothic
    Romantic hall delivered with deep green walls beside a sheet listing
    plum, walnut and brass — the room's biggest colour absent from its own
    materials list, and the operator asked how the three are even chosen.

    The sheet is written by the brief BEFORE the image exists; the editor
    sometimes goes its own way. The reconcile inserts the delivered colour
    as the lead row — inserted, never swapped, because the promised
    materials are usually in the picture too (the plum was the velvet)."""
    def room(path, wall):
        im = Image.new("RGB", (400, 500), wall)             # walls dominate
        im.paste((90, 20, 35), (40, 350, 360, 500))         # plum velvet
        im.paste((62, 39, 35), (0, 300, 400, 350))          # walnut band
        im.save(path)
        return str(path)

    spec = [("deep plum", "#4B1A28"), ("carved walnut", "#3E2723"),
            ("antique brass", "#8B6F47")]

    green = room(tmp_path / "green.png", (34, 72, 30))
    new, note = factory.reconcile_sheet(green, spec)
    assert note and "green" in note
    assert new[0][0].endswith("green") and new[0][1].startswith("#")
    assert new[1:] == spec[:3], "promised rows stay — inserted, never swapped"

    # a plum room under warm light drifts in hue but keeps its kind: agrees
    plum = room(tmp_path / "plum.png", (86, 34, 52))
    same, note2 = factory.reconcile_sheet(plum, spec)
    assert note2 == "" and same == spec

    # a neutral room makes no claim at all
    beige = room(tmp_path / "beige.png", (208, 200, 188))
    _, note3 = factory.reconcile_sheet(beige, spec)
    assert note3 == ""


def test_room_sanity_reads_the_judges_verdict(monkeypatch):
    """The two-tap kitchen (operator, 2026-08-24, queue #17): a comparison
    format renders its room once, judge_pick never sees a single candidate,
    and the colour gates cannot see fixtures — so a dedicated object-logic
    look exists. It must parse a faults list and stay quiet on a sound room."""
    from studio import factory, llm

    monkeypatch.setattr(llm, "complete", lambda *a, **k:
                        '{"problems": ["two taps on facing counters", '
                        '"dining table parked in the walkway"]}')
    faults = factory.room_sanity("/tmp/x.jpg", "june")
    assert faults == ["two taps on facing counters",
                      "dining table parked in the walkway"]

    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"problems": []}')
    assert factory.room_sanity("/tmp/x.jpg", "june") == []


def test_the_format_now_spells_out_object_logic():
    """The richness rules lived only in `structure`, which never reaches the
    renderer — the render prompt is base_scene + change + style_suffix. All
    three prompt-bearing fields must carry the object-logic discipline."""
    import yaml
    cfg = yaml.safe_load(open("config/formats/material-board.yaml",
                              encoding="utf-8"))
    for field in ("structure", "style_suffix", "base_scene_rule"):
        text = str(cfg.get(field, "")).lower()
        assert "one sink" in text or "exactly once" in text, field
    assert "asymmetric" in str(cfg["style_suffix"]).lower()
    assert "eight concrete" in str(cfg["base_scene_rule"]).lower()
