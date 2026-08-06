"""LLM access layer — one function, two providers.

  LLM_PROVIDER=claude_code  → shells out to the local `claude` CLI in
                              headless print mode; usage draws from the
                              user's Claude subscription (Max plan), no
                              API credits needed. Default.
  LLM_PROVIDER=api          → Anthropic API with ANTHROPIC_API_KEY.

Images: the API path sends base64 blocks; the claude_code path asks the
CLI to Read the files (read-only tool, allowed headlessly).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating fences/preamble."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    start = text.find("{")
    if start > 0:
        text = text[start:]
    end = text.rfind("}")
    if end != -1:
        text = text[:end + 1]
    return json.loads(text)


def complete(prompt: str, model: str | None = None,
             images: list[str] | None = None, max_tokens: int = 4000) -> str:
    provider = os.environ.get("LLM_PROVIDER", "claude_code")
    model = model or DEFAULT_MODEL
    if provider == "claude_code":
        return _via_claude_code(prompt, model, images)
    return _via_api(prompt, model, images, max_tokens)


# ── provider: local claude CLI (subscription auth) ──────────────

def _claude_bin() -> str:
    path = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not Path(path).exists():
        raise RuntimeError("claude CLI not found — install Claude Code or set LLM_PROVIDER=api")
    return path

def _via_claude_code(prompt: str, model: str, images: list[str] | None) -> str:
    if images:
        listing = "\n".join(f"  - {p}" for p in images)
        prompt = (f"First, use the Read tool to view each of these image files:\n"
                  f"{listing}\n\nThen answer the following.\n\n{prompt}")
    # An ANTHROPIC_API_KEY in the environment overrides the CLI's subscription
    # login and makes it refuse to start — strip it for this subprocess only.
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    proc = subprocess.run(
        [_claude_bin(), "-p", prompt, "--model", model, "--output-format", "json"],
        capture_output=True, text=True, timeout=420, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:300] or proc.stdout[:300]}")
    payload = json.loads(proc.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(payload.get('result'))[:300]}")
    return payload["result"]


# ── provider: Anthropic API ─────────────────────────────────────

def _via_api(prompt: str, model: str, images: list[str] | None, max_tokens: int) -> str:
    import anthropic
    if images:
        content: list = []
        for i, p in enumerate(images):
            content.append({"type": "text", "text": f"Image {i}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(Path(p).read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
    else:
        content = prompt
    client = anthropic.Anthropic()
    resp = client.messages.create(model=model, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": content}])
    return resp.content[0].text
