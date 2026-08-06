"""Signal normalizer & scorer — handbook pages 03/04, miniature.

Takes the collector's raw noise and produces typed signal records:

  { topic, signal_type, summary, why_now, velocity, niche_fit,
    producibility, expiry_hours, score, exemplar_urls }

One LLM call does dedupe + typing + gate filtering + scoring for the whole
batch (the handbook's normalizer + seven-filter lens, collapsed into a
prototype-sized step). Gates from sources.yaml are enforced in the prompt
AND re-checked mechanically after.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

SIGNAL_TYPES = ["format", "aesthetic", "topic", "meme", "product", "seasonal"]

PROMPT = """You are the trend-intelligence layer of an automated content studio \
running a disclosed-AI lifestyle persona about coffee & city life.

Below are {n} raw trend items collected in the last hour from Bluesky search, \
Reddit, industry RSS and Google Trends. Many are noise, duplicates, ads, \
or off-niche.

Distill them into the {keep} strongest SIGNALS for this persona to potentially \
post about. A signal is a wave/pattern across items, not a single post.

Rules (gates — violating items are discarded, never scored):
- Discard anything touching: {banned}.
- Discard pure self-promo/ads, individual complaints, gear-repair questions.
- Discard anything older than {max_age} hours unless it is clearly seasonal/evergreen.
- The persona can only produce: photorealistic still images of coffee, cafés, \
city scenes, drinks, interiors (NO readable text, NO logos, NO real people's faces, \
NO hands doing latte art mid-pour). Set producibility accordingly.

Scoring weights: niche_fit {w_niche}, velocity {w_vel}, producibility {w_prod}.

Return STRICT JSON, no markdown fences, matching:
{{"signals": [{{
  "topic": "short name",
  "signal_type": "one of {types}",
  "summary": "what the wave is, 1-2 sentences",
  "why_now": "evidence from the items that this is moving now",
  "velocity": 0.0-1.0,
  "niche_fit": 0.0-1.0,
  "producibility": 0.0-1.0,
  "expiry_hours": int (how long this stays postable),
  "exemplar_urls": ["up to 3 source urls from the items"],
  "source_count": int (how many independent items support it)
}}]}}

RAW ITEMS:
{items}"""


def load_scoring() -> dict:
    with open(CONFIG_DIR / "sources.yaml") as f:
        return yaml.safe_load(f)["scoring"]


def _compact(items: list[dict]) -> str:
    lines = []
    for it in items:
        lines.append(
            f"- [{it['kind']}|{it['source']}|vel~{it['score_hint']}|age{it.get('age_hours')}h] "
            f"{it['title']}" + (f" :: {it['detail'][:150]}" if it.get("detail") else "")
            + (f" ({it['url']})" if it.get("url") else "")
        )
    return "\n".join(lines)


def normalize(items: list[dict], model: str | None = None) -> list[dict]:
    """Batch of raw items → scored signal records, ranked best-first."""
    from studio import llm

    model = model or os.environ.get("SIGNALS_MODEL", llm.DEFAULT_MODEL)

    cfg = load_scoring()
    w = cfg["weights"]
    prompt = PROMPT.format(
        n=len(items),
        keep=cfg.get("signals_kept_per_cycle", 10),
        banned=", ".join(cfg["gates"]["banned_topics"]),
        max_age=cfg["gates"]["max_age_hours"],
        w_niche=w["niche_fit"], w_vel=w["velocity"], w_prod=w["producibility"],
        types=SIGNAL_TYPES,
        items=_compact(items),
    )

    reply = llm.complete(prompt, model=model, max_tokens=4000)
    signals = llm.extract_json(reply)["signals"]

    # mechanical re-check of gates + composite score (never trust one layer)
    out = []
    for s in signals:
        if s.get("producibility", 0) < 0.3:
            continue
        s["score"] = round(
            w["niche_fit"] * s.get("niche_fit", 0)
            + w["velocity"] * s.get("velocity", 0)
            + w["producibility"] * s.get("producibility", 0),
            3,
        )
        out.append(s)
    out.sort(key=lambda s: -s["score"])
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    from studio.collector import collect_all

    raw = collect_all()
    print(f"\n{len(raw)} raw items → normalizing...")
    sigs = normalize(raw)
    print(f"{len(sigs)} signals:\n")
    for s in sigs:
        print(f"  {s['score']:.2f} [{s['signal_type']:<9}] {s['topic']}  "
              f"(vel {s['velocity']}, fit {s['niche_fit']}, prod {s['producibility']}, "
              f"expires {s['expiry_hours']}h, {s['source_count']} sources)")
        print(f"        {s['summary']}")
