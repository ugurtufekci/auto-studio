"""What a slow action is doing, while it is doing it.

Publishing is synchronous and takes a minute or more: a carousel uploads one
container per slide, a Reel waits on Meta's transcode. Until now the console
showed nothing between the click and the verdict, so on 2026-08-18 the
operator watched a still screen, could not tell whether the press had
registered, and pressed approve again — starting a second publish of the
same post. The account was saved by luck rather than by design.

Two separate fixes came out of that. The draft claim in draftpool stops the
duplicate; this module stops the SILENCE, which is what caused the second
press in the first place.

Deliberately in memory and not in the ledger: it is a live view of one
machine's work, worth nothing after the fact, and the ledger is the record
of decisions rather than of progress bars.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}
_CURRENT = threading.local()

# A finished line stays readable for a moment after the action ends, so the
# console can show "published" rather than blanking the instant it is true.
KEEP_SECONDS = 90


def note(text: str, step: int = 0, total: int = 0, key: str = "") -> None:
    """Record what is happening now. Safe to call from anywhere, including
    code that has no idea whether anything is watching."""
    key = key or getattr(_CURRENT, "key", "")
    if not key:
        return
    with _LOCK:
        _STATE[key] = {"text": text, "step": step, "total": total,
                       "at": time.time()}


def get(key: str) -> dict | None:
    with _LOCK:
        state = _STATE.get(key)
        if state and time.time() - state["at"] > KEEP_SECONDS:
            _STATE.pop(key, None)
            return None
        return dict(state) if state else None


def clear(key: str) -> None:
    with _LOCK:
        _STATE.pop(key, None)


def bind(key: str) -> None:
    """Attribute everything this thread reports from now on to `key`."""
    _CURRENT.key = key


@contextmanager
def watching(key: str):
    """Everything reported inside this block belongs to `key`.

    Thread-local, so two releases on one console never write over each
    other's line — which is exactly the situation that produced the
    interleaved "slide 1/6, slide 1/6, slide 2/6" the operator saw."""
    previous = getattr(_CURRENT, "key", "")
    _CURRENT.key = key
    try:
        yield
    finally:
        _CURRENT.key = previous
