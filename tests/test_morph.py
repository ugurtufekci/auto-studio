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

def test_a_style_swap_is_a_redecoration_not_a_repaint():
    """Told to "change the materials", an editor recolours the same sofa —
    and five recoloured sofas are not five design styles."""
    restyle = factory.edit_instruction("authentic Art Deco: fluted walnut, "
                                       "brass inlay", mode="restyle")
    assert "redecorate" in restyle.lower()
    assert "same camera angle" in restyle
    assert "clear away" in restyle.lower()      # the before-room's clutter goes

    materials = factory.edit_instruction("sage green walls #9CAF88")
    assert "Change the materials" in materials
    assert "redecorate" not in materials.lower()


# ── the format, and its price ───────────────────────────────────

def test_the_format_carries_the_reference_measurements():
    look = formats.settings(formats.load("style-morph"), "june")
    assert look["assembly"] == "morph"
    assert look["before_frame"] is True
    assert (look["before_secs"], look["secs_per_frame"]) == (2.2, 3.0)
    assert look["label_hold"] == 1.2
    assert look["aspect"] == "vertical"
    assert look["cut"] == "none"           # nothing is ever cut in this format
    assert look["frames"] == [5, 5]
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


def test_june_has_adopted_the_style():
    """for_persona refuses a style the persona never signed up to, so an
    unadopted format fails deep in a run rather than at the gate."""
    assert formats.for_persona("june", "style-morph")["id"] == "style-morph"
