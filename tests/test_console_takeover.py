"""Double-clicking studio.command after a pull must actually restart it.

The failure this pins cost most of an afternoon and several rounds of
"did you restart it?". The console detected that the port was busy, printed
"the console is already running", and exited 0 — true, and useless: the
instance serving the browser had been started before the pull, so every
restart was a no-op and the operator kept hitting a bug fixed hours before.

The port being busy is not the question. WHICH CODE is holding it is.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

# Importing the console runs load_dotenv() at module level, which pulls the
# operator's real .env into os.environ — and a leaked FAL_KEY makes
# media_host look configured in tests that exist to check it is not. The
# environment is put back exactly as it was.
_before = dict(os.environ)
import serve  # noqa: E402

os.environ.clear()
os.environ.update(_before)


def test_the_same_commit_is_recognised_and_left_alone():
    assert serve.same_code("8dc60fb (2026-08-18)", "8dc60fb (2026-08-18)")
    # an uncommitted edit on one side is not staleness — the commit decides
    assert serve.same_code("8dc60fb (2026-08-18) +local edits",
                           "8dc60fb (2026-08-18)")


def test_an_older_commit_is_stale_and_gets_taken_over():
    assert not serve.same_code("440b20a (2026-08-18)", "8dc60fb (2026-08-18)")


def test_a_console_we_cannot_identify_counts_as_stale():
    """Better to take over something unidentifiable than to leave it serving
    the operator's browser while a newer console cannot bind."""
    assert not serve.same_code("unknown (not a git checkout)", "8dc60fb")
    assert not serve.same_code("", "8dc60fb")


def test_the_launcher_compares_versions_before_it_gives_up():
    """The old code exited as soon as it saw one of ours on the port. The
    version check has to sit between finding it and exiting."""
    src = (ROOT / "dashboard" / "serve.py").read_text(encoding="utf-8")
    found = src.index("running = _running_console(PORT)")
    compared = src.index("if same_code(theirs, RUNNING_CODE):")
    took_over = src.index("if _take_over(PORT):")
    assert found < compared < took_over
    # and the operator gets the manual command when it cannot do it itself
    assert "lsof -ti tcp:{PORT} | xargs kill" in src


def test_a_stranger_on_the_port_is_never_killed():
    """_take_over is only ever reached through the branch that has already
    confirmed the holder is our own console on older code."""
    src = (ROOT / "dashboard" / "serve.py").read_text(encoding="utf-8")
    guard = src.index("if running is None:")
    kill = src.index("if _take_over(PORT):")
    assert guard < kill
    assert "continue                     # a stranger" in src
