# autoStudio — AI Persona Studio prototype

A vertical slice of the Persona Studio handbook's **S0 "Spine"**, running fully
autonomously: harvest trends → score → pool → brief → generate media → publish
to Bluesky → log full lineage.

Collection is shared, publishing is per-account: one cloud harvest feeds every
persona, and each account draws from the pool of the category its persona.yaml
names — the food-drink account and the travel-places account consume different
signals from the same harvest.

```
shared harvest (cloud routine 2x/day — routines/trend-harvest.md)
   → collector.py     Reddit + Google News/Trends + YouTube + trade RSS + HN, per category
   → judge            typed & scored signals → data/signals/<category>/latest.json

per-account cycle (run.py, one per persona)
   → pool.py          reads the persona's category pool, drops expired signals
   → brain.py         top signal → brief → caption in persona voice
   → factory.py       fal.ai images (4 candidates → auto-pick) · TTS · ffmpeg slideshow · Wan clip
   → publisher.py     Bluesky post with AI-disclosure applied
   → store.py         SQLite lineage: post → asset → brief → signal
```

## Setup

1. `cp .env.example .env` and fill in the three credentials (see comments inside).
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `brew install ffmpeg` (if not installed)

## Run

- One full autonomous cycle (one post): `python run.py`
- Dry run (everything except publish): `python run.py --dry-run`
- Cycle against another category's pool: `python run.py --category travel-places`
- Collect + score in-process, no pool needed: `python run.py --live-collect`
- Inspect a pool from the shell: `python -m studio.pool [category …]`
- Skip the anti-pattern jitter delay: add `--now`
- Hero video clip (Wan text-to-video): `python run.py --hero`
- Ops console: `python dashboard/serve.py` → http://localhost:8377
- Provision a persona's profile (avatar, bio, pinned intro): `python provision.py`
- Guardrail state / reset warm-up: `python -m studio.guard [--reset-warmup]`

## Scheduling (local, macOS)

```bash
./scheduler/install.sh install     # 08:00 + 16:00 daily
./scheduler/install.sh status
./scheduler/install.sh uninstall
```

Mode comes from `SCHEDULER_MODE` in `.env` (`dry_run` | `live`) — no reinstall
needed to switch. Caveat: launchd cannot fire while the machine is asleep; it
runs the missed job once on wake. Genuine 24/7 posting needs an always-on host
(server or a Claude cloud routine).

## Data sources

Six unauthenticated sources per niche. Collection never uses a persona's
credentials — those are for the publish call only.

| Source | Mechanism | Yield |
|---|---|---|
| Reddit | `r/a+b+c/hot.rss` — one combined request per niche | ~50 |
| Google News | `news.google.com/rss/search?q=…` per query | ~20/query |
| Google Trends | `trends.google.com/trending/rss`, niche-gated | 0–3 |
| YouTube | `feeds/videos.xml` per channel (handles resolved + cached) | ~6/channel |
| Industry RSS | trade press per niche | ~8/feed |
| Hacker News | Algolia search with points, niche-gated | ~10 |

Broad-catchment sources (country-wide trending searches, a tech forum) are
filtered against keywords derived from the niche config — an empty result from
those is the correct result, not a failure.

Categories live in `config/categories/*.yaml`. Adding one needs no code change:

```bash
python -m studio.collector                          # every category
python -m studio.collector food-drink travel-places # selected categories
python run.py --category wellness-fitness           # one cycle against a category's pool
```

Measured: 699 relevant items across 5 niches in a single pass.

## Configuration is the persona

- `config/persona.yaml` — who the character is: voice, disclosure, visual world
- `config/sources.yaml` — where trends come from and how they're scored

Change these, touch no code — that's the handbook's "persona is configuration"
principle demonstrated.

## Handbook mapping

| This prototype | Handbook page |
|---|---|
| collector + signals | 03 Trend intelligence |
| scoring gates/weights | 04 Signal routing |
| persona.yaml + brain | 06 Persona core (mini-bible) |
| factory | 07/08 Production + Asset factory |
| publisher + disclosure | 10 Identity & disclosure, 12 Publishing |
| SQLite lineage + dashboard | 19 Data model — "why does this post exist" |
