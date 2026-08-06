# autoStudio — AI Persona Studio prototype

A vertical slice of the Persona Studio handbook's **S0 "Spine"**, running fully
autonomously: collect trends → score → brief → generate media → publish to
Bluesky → log full lineage.

```
scheduler (2x/day)
   → collector.py     Reddit hot + Google Trends + coffee RSS
   → signals.py       LLM normalizes → typed signals (velocity, expiry, score)
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
