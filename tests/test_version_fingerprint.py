"""The "older code" banner must mean the CODE changed, nothing else.

The ledger commits data all day — every approve, reject and cycle advances
HEAD — and the console compared full version strings, so the banner said
"this console is running older code · restart" right after every approve,
including the one a CEO was watching. The fingerprint reads only the code
paths' git tree ids plus uncommitted edits under them: data commits leave
it unchanged, a real code change (committed or not) moves it.
"""

from studio import version


def _feed(monkeypatch, trees, diff):
    def fake(*args):
        return trees if args[0] == "ls-tree" else diff
    monkeypatch.setattr(version, "_git", fake)


def test_a_data_only_commit_does_not_move_the_fingerprint(monkeypatch):
    _feed(monkeypatch, "040000 tree aaa\tdashboard\n040000 tree bbb\tstudio", "")
    before = version.code_fingerprint()
    # a ledger commit advances HEAD, but the code trees keep their ids
    _feed(monkeypatch, "040000 tree aaa\tdashboard\n040000 tree bbb\tstudio", "")
    assert version.code_fingerprint() == before


def test_a_committed_code_change_moves_it(monkeypatch):
    _feed(monkeypatch, "040000 tree aaa\tdashboard\n040000 tree bbb\tstudio", "")
    before = version.code_fingerprint()
    _feed(monkeypatch, "040000 tree aaa\tdashboard\n040000 tree ccc\tstudio", "")
    assert version.code_fingerprint() != before


def test_an_uncommitted_code_edit_moves_it_too(monkeypatch):
    """A dev editing serve.py without committing is exactly as stale as a
    pulled fix — the old string compare flagged this case by accident and
    the fingerprint must keep flagging it on purpose."""
    _feed(monkeypatch, "040000 tree aaa\tdashboard", "")
    before = version.code_fingerprint()
    _feed(monkeypatch, "040000 tree aaa\tdashboard", "diff --git a/dashboard/serve.py …")
    assert version.code_fingerprint() != before


def test_outside_a_checkout_it_says_unknown(monkeypatch):
    _feed(monkeypatch, "", "")
    assert version.code_fingerprint() == "unknown"
