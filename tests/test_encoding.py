"""Every text-mode file touch must name utf-8 — no locale roulette.

The ledger, configs, and persona bibles cross machines: a Linux container
writes them, a Windows laptop reads them. Python's default text encoding is
the machine's locale (cp1254 on a Turkish Windows), which once turned the
🤖 disclosure into 'ğŸ¤–' in the operator's console — and would have
published that way. This test freezes the fix: any open()/read_text()/
write_text() in studio/ or dashboard/ without an explicit encoding (or
binary mode) fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = sorted(list((ROOT / "studio").glob("*.py"))
                 + list((ROOT / "dashboard").glob("*.py")))


def _mode_of(call: ast.Call) -> str:
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return "r"


def _has_encoding(call: ast.Call) -> bool:
    return any(kw.arg == "encoding" for kw in call.keywords)


def test_all_text_io_names_utf8():
    assert SCANNED, "nothing to scan — did the layout change?"
    offenders = []
    for path in SCANNED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            # builtin open(...) — binary modes are exempt
            if isinstance(f, ast.Name) and f.id == "open":
                if "b" not in _mode_of(node) and not _has_encoding(node):
                    offenders.append(f"{path.name}:{node.lineno} open()")
            # Path.read_text()/.write_text() — always text, always need it
            elif (isinstance(f, ast.Attribute)
                  and f.attr in ("read_text", "write_text")
                  and not _has_encoding(node)):
                offenders.append(f"{path.name}:{node.lineno} {f.attr}()")
    assert not offenders, (
        "text IO without explicit encoding='utf-8' (breaks on non-UTF-8 "
        "locales, e.g. Turkish Windows): " + ", ".join(offenders))
