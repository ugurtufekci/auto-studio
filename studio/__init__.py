"""autoStudio — the studio package.

The version check is here because it is the first thing every entry point
imports, and because the failure it replaces is genuinely misleading. On
macOS `python3` is Xcode's 3.9, and the studio runs under a 3.11+ venv; the
same repository therefore imports fine one way and dies the other with

    ImportError: cannot import name 'UTC' from 'datetime'

which names a stdlib symbol and says nothing about interpreters. An operator
reading that on 2026-08-18 reasonably concluded the pull had broken
something.
"""

import sys

MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:
    raise RuntimeError(
        f"autoStudio needs Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or "
        f"newer; this is {sys.version.split()[0]} at {sys.executable}. On "
        f"macOS a bare `python3` is Xcode's own 3.9 — activate the project "
        f"venv (`source .venv/bin/activate`) or call its interpreter "
        f"directly (`.venv/bin/python`)."
    )
