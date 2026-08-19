"""The morph reel — a real continuous video, not a slideshow.

The operator's note on the first attempt was exact: "artık slide show gibi
değil düz video olması lazım". So what these pin is the difference: that the
timeline matches the beat measured off the reference reel, that the type
lands where the reference puts it, that a transition is a generated clip
retimed to the beat rather than a cut, and that a style costing seven times
the others cannot wander into an unattended nightly rotation.
"""

from pathlib import Path

import pytest

from studio import factory, formats

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _still(path: Path, colour=(120, 110, 100)) -> str:
    Image.new("RGB", (1080, 1920), colour).save(path)
    return str(path)


# ── the beat ────────────────────────────────────────────────────

def test_the_timeline_matches_the_reference_reel():
    """Measured off the file the operator uploaded: names at 4.0, 7.0, 10.0,
    13.0 and 16.0 seconds, each holding about 1.2s, after a 2.2s opening on
    the before-room. Getting this wrong is not a rendering bug — it is the
    format being a different format."""
    tl = factory.morph_timeline(2.2, 3.0, 5, 1.2)
    assert [round(end, 1) for _, end in tl] == [5.2, 8.2, 11.2, 14.2, 17.2]
    assert all(round(end - start, 1) == 1.2 for start, end in tl)
    # the name belongs to the ARRIVAL, never to the transition: put it
    # earlier and it labels a room that is still half the previous style
    assert tl[0][0] > 2.2 + 3.0 / 2


def test_a_style_name_is_what_reaches_the_screen_not_its_materials():
    """The label carries the hex codes so the carousel twin can print them.
    The video has a second and a half — it gets the two words the voice also
    says."""
    label = "Art Deco · fluted walnut #6B4A2F · aged brass #B08D57"
    assert factory.style_name(label) == "Art Deco"
    assert factory.style_name("") == ""


# ── the type ────────────────────────────────────────────────────

def test_the_caption_is_centred_at_the_measured_height(tmp_path):
    """White, centred, about 65% down the frame, no plate — the reference
    draws it that way and a label pinned low is covered by the app's own
    button rail."""
    png = factory.caption_png("Art Deco", tmp_path / "c.png", factory.VERTICAL, 0.65)
    img = Image.open(png).convert("RGBA")
    assert img.size == factory.VERTICAL
    rows = [y for y in range(0, 1920, 8)
            if any(img.getpixel((x, y))[3] > 200 for x in range(0, 1080, 8))]
    assert rows, "nothing was drawn"
    centre = (rows[0] + rows[-1]) / 2 / 1920
    assert 0.58 < centre < 0.72
    # and it is white type, not a box: most of the frame stays transparent
    opaque = sum(1 for y in range(0, 1920, 16) for x in range(0, 1080, 16)
                 if img.getpixel((x, y))[3] > 200)
    assert opaque < 0.20 * (1920 // 16) * (1080 // 16)


def test_a_long_opening_line_wraps_instead_of_shrinking_to_a_whisper(tmp_path):
    """A style name is two words and stays big. An opening line is six, and
    shrunk onto one line it is unreadable in the 2.2s it has — so it breaks
    over two lines at a size that still reads."""
    short = Image.open(factory.caption_png("Art Deco", tmp_path / "a.png"))
    long_one = Image.open(
        factory.caption_png("one of these five is mine", tmp_path / "b.png"))

    def band(img):
        rows = [y for y in range(0, 1920, 4)
                if any(img.getpixel((x, y))[3] > 200 for x in range(0, 1080, 4))]
        return rows[-1] - rows[0]

    # two lines are taller than one — proof it wrapped rather than shrank
    assert band(long_one) > band(short) * 1.4


# ── assembly ────────────────────────────────────────────────────

def test_a_transition_is_retimed_to_the_beat_not_cut(tmp_path):
    """The generators' floor is five seconds and the beat is three, so every
    transition is played faster than it was made. Frames are not dropped —
    the clip is retimed, so the motion stays continuous."""
    src = factory.still_clip(_still(tmp_path / "s.png"), 5.0, tmp_path / "src.mp4")
    assert factory.clip_seconds(src) == pytest.approx(5.0, abs=0.15)
    out = factory.retime(src, 3.0, tmp_path / "out.mp4")
    assert factory.clip_seconds(out) == pytest.approx(3.0, abs=0.15)
    assert Image.open(_frame(out, tmp_path)).size == factory.VERTICAL


def _frame(video: str, tmp_path: Path) -> str:
    import subprocess
    dest = tmp_path / "probe.png"
    subprocess.run([factory.ffmpeg_bin(), "-y", "-ss", "0.2", "-i", str(video),
                    "-frames:v", "1", str(dest)], check=True, capture_output=True)
    return str(dest)


def test_the_graph_concatenates_and_gates_each_label_to_its_own_window(tmp_path):
    """One encode for the whole reel: written out per label instead, the type
    would soften under another generation of h264 on exactly the frames it
    matters on. And each overlay is switched on only for its style's hold —
    a label left on is a label over the wrong room."""
    clips = [str(tmp_path / f"c{i}.mp4") for i in range(3)]
    overlays = [(str(tmp_path / "l0.png"), 4.0, 5.2),
                (str(tmp_path / "l1.png"), 7.0, 8.2)]
    cmd = factory.morph_command(clips, overlays, None, tmp_path / "out.mp4")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=0" in graph
    assert "between(t,4.000,5.200)" in graph
    assert "between(t,7.000,8.200)" in graph
    # no crossfade anywhere: this format's whole claim is that there are no cuts
    assert "xfade" not in graph


def test_the_track_is_stereo_and_the_names_land_on_their_own_timestamps(tmp_path):
    """A mono TTS clip arriving first turns the whole reel's track mono —
    invisible in review, audible on every phone. And the names are delayed
    to the moment their room arrives rather than read as one script: that is
    the difference between naming what you see and talking over it."""
    import subprocess

    beep = tmp_path / "beep.m4a"
    subprocess.run([factory.ffmpeg_bin(), "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.5", str(beep)],
                   check=True, capture_output=True)
    out = factory.morph_audio([(str(beep), 4.0), (str(beep), 7.0)], None,
                              17.2, tmp_path / "mix.m4a")
    probe = subprocess.run([factory.ffmpeg_bin(), "-hide_banner", "-i", out],
                           capture_output=True, text=True).stderr
    assert "stereo" in probe
    assert factory.clip_seconds(out) == pytest.approx(17.2, abs=0.2)


# ── the instruction that makes a style a style ──────────────────

def test_a_style_swap_re_skins_the_room_and_moves_nothing():
    """This one was got backwards first and the operator caught it.

    Asked for the FURNITURE SHAPES to change, every frame came back with
    different furniture in different places — and a video model handed two
    unrelated rooms can only cross-fade between them, which is what made
    the reel read as consecutive photographs. The reference does the
    opposite: one fixed room whose surfaces re-skin in place, "sanki üstünü
    çıkarır gibi". Proven on a single pair afterwards — the floor veined
    into marble first, then the walls turned walnut, then the upholstery
    went green, each surface on its own beat, nothing moving."""
    reskin = factory.edit_instruction("authentic Art Deco: fluted walnut, "
                                      "brass inlay", mode="reskin")
    assert "Re-skin every surface" in reskin
    assert "DO NOT move, remove, add or reshape" in reskin
    assert "same silhouette" in reskin
    assert "Same camera, same framing" in reskin
    assert "Tidy away loose clutter" in reskin
    # and it must NOT invite a refurnishing
    assert "redecorate" not in reskin.lower()

    materials = factory.edit_instruction("sage green walls #9CAF88")
    assert "Re-skin" not in materials
    # and the materials mode opens on the walls, because the walls are what
    # kept not changing: four schemes of a library nook differed only in
    # their floor while every label promised the walls swapping too
    assert materials.startswith("START WITH THE WALLS")
    assert "never one colour over the whole room" in materials


def test_the_morph_prompt_is_the_string_that_was_proven():
    """Two attempts to improve this cost $2.00 and both came back with a
    sheet of white smoke sweeping the room. "As if a covering were being
    pulled off" made the model draw a covering; "no smoke, no fog, no cloth"
    made it draw smoke, because a video model reads a negation as a subject.
    Name what the surfaces do and nothing else."""
    assert factory.MORPH_PROMPT == (
        "the furniture, materials and finishes of the room transform "
        "smoothly in place from one interior style into the other, "
        "the camera is locked and does not move, no cuts, no people")
    for summoned in ("smoke", "fog", "cloth", "covering", "wipe", "curtain"):
        assert summoned not in factory.MORPH_PROMPT


def test_a_wiped_transition_is_caught_on_the_first_clip(tmp_path):
    """The operator's rule, and a fair one: my mistakes must not cost them
    money. A bad transition prompt was bought five times over, twice, before
    anyone could see it. The first clip is the sample now — $0.20 instead of
    $1.00 — and the check is arithmetic on brightness, so it is free."""
    import subprocess

    first = _still(tmp_path / "a.png", (60, 55, 50))      # a dim room
    last = _still(tmp_path / "b.png", (70, 60, 55))       # and a dim room
    ceiling = max(factory.mean_luminance(first),
                  factory.mean_luminance(last)) + factory.WIPE_MARGIN

    # a transition that stays between its two rooms is a re-skin
    ok = factory.still_clip(first, 2.0, tmp_path / "ok.mp4")
    assert factory.looks_wiped(ok, first, last, tmp_path) == ""

    # one that whites out in the middle is a wipe, whatever it was asked for
    white = _still(tmp_path / "w.png", (250, 250, 250))
    flash = factory.still_clip(white, 2.0, tmp_path / "flash.mp4")
    verdict = factory.looks_wiped(flash, first, last, tmp_path)
    assert verdict and "whites out" in verdict
    assert factory.mean_luminance(white) > ceiling


def test_the_first_transition_is_checked_before_the_rest_are_bought():
    import inspect

    src = inspect.getsource(factory.make_morph_video)
    assert src.index("looks_wiped(") < src.index("clips.append(retime(")
    assert "stopping after ONE transition" in src


# ── the format, and its price ───────────────────────────────────

def test_the_format_carries_the_reference_measurements():
    look = formats.settings(formats.load("style-morph"), "june")
    assert look["assembly"] == "morph"
    # The beat and the shape are the reference's. The before-room is not:
    # the operator turned it off on 2026-08-19 for the villa hall — "oranin
    # bos hali olmayacak, 6 farkli tarz istiyorum senden" — so the reel opens
    # on the first style and six styles buy five transitions, not six.
    assert look["before_frame"] is False
    assert (look["before_secs"], look["secs_per_frame"]) == (3.0, 3.0)
    assert look["label_hold"] == 1.2
    assert look["aspect"] == "vertical"
    assert look["cut"] == "none"           # nothing is ever cut in this format
    assert look["frames"] == [6, 6]
    assert look["voice_mode"] == "names"   # the voice says the style, nothing else
    assert look["music"] is True


def test_the_expensive_style_stays_out_of_the_unattended_rotation():
    """Every other style costs about fifteen cents a reel; this one buys five
    generated transitions and costs about a dollar. It is shootable on
    purpose with --style and must not be something a nightly cycle drifts
    into — the operator was explicit about not spending on fal by accident."""
    assert formats.load("style-morph").get("auto_rotate") is False
    for other in ("material-board", "colourway", "style-swap"):
        assert formats.load(other).get("auto_rotate", True) is True


def test_the_style_replaces_the_brief_rules_it_cannot_live_with(monkeypatch):
    """The shared brief contract is written for a colourway comparison and
    fights this format in both directions. Measured on the first real run:
    the before-room came back pleasant and tidy — no hook — and the five
    styles came back as five repaints with the furniture unchanged.

    Both were obedience. The default forbids finishes in base_scene, and a
    tired room IS its worn carpet; the default forbids touching furniture,
    which is exactly what a design language does. So a style replaces those
    rules rather than arguing with them from an appendix."""
    from studio import brain, llm

    seen = {}

    def fake_complete(prompt, model=None, max_tokens=0, images=None):
        seen["prompt"] = prompt
        return ('{"premise":"p","angle":"a","mood":"m","caption":"c",'
                '"alt_text":"alt","voiceover_script":"",'
                '"base_scene":"a room","opening_line":"this room, five ways",'
                '"frame_swaps":[{"change":"authentic Art Deco: fluted walnut",'
                '"label":"Art Deco · walnut #6B4A2F"},'
                '{"change":"authentic Moroccan: carved plaster",'
                '"label":"Moroccan · plaster #D9C7A7"},'
                '{"change":"c","label":"C · x #111111"},'
                '{"change":"d","label":"D · x #222222"},'
                '{"change":"e","label":"E · x #333333"},'
                '{"change":"f","label":"F · x #444444"}],'
                '"image_prompts":[]}')

    monkeypatch.setattr(llm, "complete", fake_complete)
    signal = {"topic": "t", "signal_type": "topic", "summary": "s", "why_now": "w"}
    brief = brain.make_brief(signal, "slideshow_video", persona_id="june",
                             style=formats.load("style-morph"))
    # the rules are wrapped yaml/docstring text by the time they reach the
    # prompt, so compare on collapsed whitespace rather than on line breaks
    p = " ".join(seen["prompt"].split())
    # the style's own rules are in. The anchor room stopped being a tired
    # "before" when the operator turned that frame off — it is never shown
    # now — so what it must insist on is size and density instead.
    assert "LARGE, IMPRESSIVE AND FULL" in p
    assert "never shown" in p and "is not a \"before\"" in p
    assert "OPENS WITH THE STYLE'S NAME" in p
    assert "A RE-SKIN OF THE SAME ROOM, NOT A REFURNISHING" in p
    assert "What changes is what they are MADE OF" in p
    # and the defaults they replace are OUT, not sitting alongside them
    assert "NO colours, finishes or materials that a frame is going to change" not in p
    assert "never re-describe the room, the camera or the furniture" not in p
    # the opening line is asked for, and comes back on the brief
    assert "opening_line" in p
    assert brief["opening_line"] == "this room, five ways"
    # the anchor still needs the scene as a prompt of its own — it is what
    # every style is edited from, whether or not it reaches the screen
    assert brief["base_prompt"].startswith("a room")


def test_the_other_styles_keep_the_default_contract(monkeypatch):
    """A style that never asked for an override must see exactly the prompt
    it always did — the point of the placeholders is that they default."""
    from studio import brain, llm

    seen = {}

    def fake_complete(prompt, model=None, max_tokens=0, images=None):
        seen["prompt"] = prompt
        return ('{"premise":"p","angle":"a","mood":"m","caption":"c",'
                '"alt_text":"alt","voiceover_script":"","base_scene":"a room",'
                '"frame_swaps":[{"change":"sage walls","label":"sage #9CAF88"},'
                '{"change":"olive walls","label":"olive #6B8E23"},'
                '{"change":"ink walls","label":"ink #1B3B6F"},'
                '{"change":"clay walls","label":"clay #B66A50"}],'
                '"image_prompts":[]}')

    monkeypatch.setattr(llm, "complete", fake_complete)
    brain.make_brief({"topic": "t", "signal_type": "topic", "summary": "s",
                      "why_now": "w"}, "slideshow_video", persona_id="june",
                     style=formats.load("colourway"))
    p = " ".join(seen["prompt"].split())
    assert "NO colours, finishes or materials that a frame is going to change" in p
    assert "never re-describe the room, the camera or the furniture" in p
    assert "opening_line" not in p       # not every style has one


def test_two_styles_from_one_family_are_caught_before_anything_renders():
    """The distance gate finds a repeated room too — but only after both have
    been paid for, and the reel then goes out with four styles instead of
    five. That is what happened on the second real run: Scandinavian and a
    second pale minimal room, one of them thrown away after rendering.
    Reading the names costs nothing."""
    from studio import brain

    fmt = formats.load("style-morph")
    clash = brain.family_clashes(
        ["Scandinavian · oak #C9A227", "Japandi · plaster #E8E0D5",
         "Art Deco · brass #B08D57", "Industrial · steel #4A4A4A",
         "Moroccan · zellige #1F6F5C"], fmt)
    assert len(clash) == 1
    assert "Japandi" in clash[0] and "Scandinavian" in clash[0]

    assert brain.family_clashes(
        ["Scandinavian · oak #C9A227", "Art Deco · brass #B08D57",
         "Moroccan · zellige #1F6F5C", "Industrial · steel #4A4A4A",
         "French Country · linen #E8DCC4"], fmt) == []

    # a style outside the list is left alone — the point is to catch
    # "Scandinavian AND Japandi", not to make the brain pick from a menu
    assert brain.family_clashes(["Cabincore · pine #C19A6B",
                                 "Barbiecore · pink #FF69B4"], fmt) == []
    # and a format with no families has no opinion at all
    assert brain.family_clashes(["Scandinavian", "Japandi"], None) == []


def test_a_second_cream_style_is_caught_by_its_own_hex_codes():
    """What counts as too pale depends on what the reel opens on.

    WITH a before-room, no style may be pale: that room is beige by
    construction and cream on beige is not a change. Of five styles on the
    fourth real run, the only two that failed the change gate were the two
    pale ones — Japandi at 1.2 from the base room and French Country at 5.9,
    against a floor of 18.

    WITHOUT one there is no beige to escape, so one pale style is fine and
    two are not. Sixth run, the villa hall: "Scandinavian" at 0.82 and
    "French Neoclassical" at 0.84 came back as the same cream hall twice —
    past the distance gate, but one of them was a wasted frame and a wasted
    $0.20 morph.

    The labels already carry hex codes, so this is arithmetic on text."""
    from studio import brain

    fmt = dict(formats.load("style-morph"))
    labels = ["Moroccan · terracotta #B85C38 · brass #B08D57",
              "Japandi · sage-cream #DCDCCF · pale ash #E6DCC8",
              "Art Deco · emerald #0E5C4A · marble #1A1A1A",
              "French Country · cream #F2EADF · pale walnut #E8DCC4",
              "Industrial · brick #8B4A3B · steel #3A3A3A"]

    with_before = brain.pale_clashes(labels, dict(fmt, before_frame=True))
    assert len(with_before) == 2
    assert any("Japandi" in f for f in with_before)
    assert any("French Country" in f for f in with_before)
    assert brain.pale_clashes(["Scandinavian · chalk #EFEAE2"],
                              dict(fmt, before_frame=True))

    # opening on a style instead: one pale is allowed, two name each other
    without = brain.pale_clashes(labels, dict(fmt, before_frame=False))
    assert len(without) == 1
    assert "Japandi" in without[0] and "French Country" in without[0]
    assert "at most one may be pale" in without[0]
    assert brain.pale_clashes(["Scandinavian · chalk #EFEAE2"],
                              dict(fmt, before_frame=False)) == []
    assert brain.pale_clashes(
        ["Moroccan · terracotta #B85C38", "Art Deco · emerald #0E5C4A",
         "Industrial · brick #8B4A3B", "Victorian · oxblood #6B2737",
         "Maximalist · teal #14555A"], fmt) == []

    # and a format that never opted into the discipline has no opinion
    assert brain.pale_clashes(["A · cream #F2EADF", "B · ivory #F5F0E8"],
                              formats.load("colourway")) == []


def test_approved_keyframes_can_be_reused_without_paying_again():
    """--frames-only and --from-run are one tool in two halves: iterate the
    cheap stage for ~$0.15, then buy the transitions once against the set
    that was actually approved. Re-rendering to buy them would cost the
    $0.15 again AND change the pictures the operator just said yes to."""
    from pathlib import Path as P

    text = (P(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    assert "--from-run" in text and "--frames-only" in text
    reuse = text.index("reusing {len(cands)} keyframes")
    buy = text.index("factory.make_morph_video(")
    assert reuse < buy


def test_an_upload_is_retried_before_a_paid_run_is_thrown_away(monkeypatch):
    """One socket timeout on the way back up killed a run six seconds into
    assembly, with six paid renders already on disk and "timed out" as the
    only explanation."""
    import fal_client

    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("timed out")
        return "https://fal.example/x.jpg"

    monkeypatch.setattr(fal_client, "upload_file", flaky)
    monkeypatch.setattr(factory.time, "sleep", lambda s: None)
    assert factory.upload("/tmp/x.jpg") == "https://fal.example/x.jpg"
    assert calls["n"] == 3

    calls["n"] = 0
    monkeypatch.setattr(fal_client, "upload_file",
                        lambda p: (_ for _ in ()).throw(TimeoutError("timed out")))
    with pytest.raises(RuntimeError, match="upload failed after 3"):
        factory.upload("/tmp/x.jpg")


def test_the_expensive_stage_is_never_bought_for_a_reel_already_short():
    """The transitions are ~$1.00 of a ~$1.15 reel; everything before them
    is ~$0.15. On 2026-08-17 a run lost two of five styles to the change
    gate and then bought three transitions anyway — $0.60 spent on a
    product already known to be the wrong format. The runner now refuses,
    and --frames-only makes iterating cost the $0.15 instead."""
    import ast
    from pathlib import Path as P

    src = P(__file__).resolve().parent.parent / "run.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    text = src.read_text(encoding="utf-8")
    # the gate is ahead of make_morph_video, not after it
    gate = text.index("only {len(chosen_paths)} of {need} styles survived")
    buy = text.index("factory.make_morph_video(")
    assert gate < buy, "the spend gate must come before the spend"
    assert "--frames-only" in text
    assert any(isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "no transitions bought" in n.value
               for n in ast.walk(tree))


def test_a_one_slide_comparison_is_never_queued():
    """The morph style has refused a short reel since it was built. The
    cut-based styles went on assembling whatever survived — and a run that
    lost three of four schemes to the flood gate still produced a one-slide
    "comparison" and put it in the queue."""
    from pathlib import Path as P

    src = (P(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    assert "MIN_COMPARISON_FRAMES = 3" in src
    gate = src.index("a comparison needs at least")
    build = src.index("video_path = factory.make_slideshow(")
    assert gate < build


def test_june_has_adopted_the_style():
    """for_persona refuses a style the persona never signed up to, so an
    unadopted format fails deep in a run rather than at the gate."""
    assert formats.for_persona("june", "style-morph")["id"] == "style-morph"


def test_opening_on_a_style_names_it_at_once_and_buys_one_morph_fewer():
    """The operator turned the before-room off: "oranin bos hali olmayacak,
    6 farkli tarz istiyorum senden". Six styles with no before is FIVE
    transitions — the before-room was a paid morph too — and the first
    style's name has no transition to wait for."""
    beats = factory.morph_timeline(3.0, 3.0, 6, 1.2, lead_is_style=True)
    assert len(beats) == 6
    assert beats[0] == (0.25, 1.45)          # named as the reel opens
    assert [round(e) for _, e in beats[1:]] == [6, 9, 12, 15, 18]
    assert all(round(e - s, 2) == 1.2 for s, e in beats)
    assert beats[-1][1] == 3.0 + 5 * 3.0     # and that is the whole running time

    # with a before-room the first style still waits for its morph
    with_before = factory.morph_timeline(2.2, 3.0, 5, 1.2)
    assert with_before[0] == (4.0, 5.2)      # the reference's own first name


def test_the_reel_can_be_built_without_a_before_room(tmp_path, monkeypatch):
    """The before-room is optional end to end, not just in the timeline: six
    keyframes and no before must buy five transitions, not six."""
    bought = []

    def fake_morph_clip(a, b, dest):
        bought.append((a, b))
        Path(dest).write_bytes(b"clip")
        return str(dest), "fake-model", 0.20

    monkeypatch.setattr(factory, "upload", lambda p: f"url://{Path(p).name}")
    monkeypatch.setattr(factory, "morph_clip", fake_morph_clip)
    monkeypatch.setattr(factory, "looks_wiped", lambda *a, **k: "")
    monkeypatch.setattr(factory, "retime", lambda src, s, d, c: str(d))
    monkeypatch.setattr(factory, "still_clip", lambda src, s, d, c: str(d))
    monkeypatch.setattr(factory, "morph_audio", lambda *a, **k: "")
    monkeypatch.setattr(factory, "morph_command",
                        lambda *a, **k: ["true"])

    styled = []
    for i in range(6):
        p = tmp_path / f"style{i}.png"
        Image.new("RGB", (108, 192), (20 * i, 60, 90)).save(p)
        styled.append(str(p))

    built = factory.make_morph_video(
        None, styled, [f"Style {i} · x #11223{i}" for i in range(6)],
        tmp_path, before_secs=3.0, secs_per_style=3.0,
        voice=False, music=False, canvas=(108, 192))

    assert len(bought) == 5, "six styles, no before → five transitions"
    assert built["spend"] == 1.0
    assert built["seconds"] == 18.0


def test_the_morph_style_never_takes_a_fixed_palette_from_the_draw():
    """The persona's draw fixes ONE palette per post — right for a comparison
    of schemes in one room, exactly wrong here. Handed "charcoal and
    saffron", the first villa-hall run returned six named styles (Art Deco,
    Moroccan, Modernist, Spanish Colonial) that all printed the SAME four
    hex codes, which is the sameness the draw exists to prevent."""
    from studio import brain

    assert "palettes" in formats.load("style-morph").get("skip_draw", [])
    drawn = brain.draw_variables("june", seed=3,
                                 skip=formats.load("style-morph")["skip_draw"])
    assert "palettes" not in drawn and "palettes_spec" not in drawn
    # the room and its bones still come from the draw — only the colour goes
    assert {"rooms", "views", "architectural_moves"} <= set(drawn)

    # and a format that says nothing still gets the palette, as before
    assert "palettes" in brain.draw_variables("june", seed=3)
