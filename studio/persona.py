"""Persona identity — "persona is configuration", now plural.

One file per persona under `config/personas/<id>.yaml`. The id is the
internal, permanent name from `config/naming.md` §1: it never changes, even
when a public handle does. `config/accounts.yaml` links a persona to the
platform accounts it speaks through.

The persona owns its category, and its accounts inherit it — a persona is a
point of view, and a point of view has one subject. Nothing else in the repo
should carry a second copy of that fact.

Everything that speaks as a persona takes one explicitly: the disclosure line
is per-persona, and a studio that mixed two characters' voices — or worse,
published one persona's disclosure under another's name — would break the one
invariant this project cannot break. There is a default (a single-persona
checkout should not have to think about ids), but it is resolved once and
passed down, never re-read implicitly deep in a call stack.

    python -m studio.persona          # list personas and their categories
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PERSONA_DIR = CONFIG_DIR / "personas"


def available() -> list[str]:
    """Persona ids that have a config on disk."""
    return sorted(p.stem for p in PERSONA_DIR.glob("*.yaml"))


def default_id() -> str:
    """The persona a command speaks as when it was not told.

    PERSONA in the environment wins, so a deployment that runs one character
    never needs the flag. Otherwise the first configured persona — stable
    because `available()` sorts.
    """
    want = os.environ.get("PERSONA", "").strip()
    ids = available()
    if want:
        if want in ids:
            return want
        raise ValueError(f"PERSONA={want!r} has no config — available: {', '.join(ids)}")
    if not ids:
        raise FileNotFoundError(f"no persona configs in {PERSONA_DIR}")
    return ids[0]


@lru_cache(maxsize=8)
def _read(persona_id: str) -> dict:
    path = PERSONA_DIR / f"{persona_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"unknown persona '{persona_id}' ({path}) — "
            f"available: {', '.join(available()) or 'none'}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["id"] = persona_id
    return data


def load(persona_id: str | None = None) -> dict:
    """The persona mini-bible, with its id attached."""
    return dict(_read(persona_id or default_id()))


def category_of(persona_id: str | None = None) -> str:
    """The signal pool this persona draws from."""
    return (load(persona_id).get("content") or {}).get("category", "")


def name_of(persona_id: str | None = None) -> str:
    return (load(persona_id).get("identity") or {}).get("name", persona_id or "")


if __name__ == "__main__":
    for pid in available():
        p = load(pid)
        ident = p.get("identity") or {}
        print(f"{pid:<10} {ident.get('name',''):<8} {category_of(pid):<16} "
              f"@{ident.get('handle','—')}")
