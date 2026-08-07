# Cloud Probe v3 — Real Pipeline Test

Date: 2026-08-07
Branch under test: `feat/multi-niche-collector` @ `27f6204` ("feat(collector): multi-niche collection with six verified sources")

Scope: unlike the earlier probes (synthetic URLs, ffmpeg-missing check only), this run
exercises the actual project code — `studio.collector` and `studio.factory.make_slideshow`
— against real network sources, and checks whether a pip-only ffmpeg can satisfy the
project's own video assembly path. No project source files were modified.

## Setup

```
$ git fetch origin && git checkout feat/multi-niche-collector
Switched to a new branch 'feat/multi-niche-collector'
$ git log --oneline -1
27f6204 feat(collector): multi-niche collection with six verified sources
```

```
$ pip3 install --quiet --target /tmp/libs -r requirements.txt 2>&1 | tail -3
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting
behaviour with the system package manager. It is recommended to use a virtual environment
instead: https://pip.pypa.io/warnings/venv
```
(no errors — all requirements installed cleanly)

```
$ pip3 install --quiet --target /tmp/libs imageio-ffmpeg 2>&1 | tail -5
WARNING: Running pip as the 'root' user ... (same venv warning, no errors)

$ PYTHONPATH=/tmp/libs python3 -c "import imageio_ffmpeg, subprocess; ..."
FFMPEG_EXE /tmp/libs/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
ffmpeg version 7.0.2-static https://johnvansickle.com/ffmpeg/  Copyright (c) 2000-2024 the FFmpeg developers
```

`imageio-ffmpeg` installs a static, statically-linked ffmpeg 7.0.2 binary with no root/apt
required. It runs directly.

## Test 1 — Real collector (coffee)

```
$ PYTHONPATH=/tmp/libs:. python3 -m studio.collector coffee 2>&1

niches: coffee

── coffee ──
  [collector] reddit: 50 items
  [collector] google news: 80 items
  [collector] trends: dropped 10 off-niche trending terms
  [collector] google trends: 0 items
  [collector] youtube: 12 items
  [collector] rss Perfect Daily Grind: 0 entries (http 202)
  [collector] industry rss: 24 items
  [collector] hn: dropped 20 off-niche stories
  [collector] hacker news: 10 items
  → 176 items

TOTAL: 176 raw items across 1 niches
by kind: {'news': 80, 'reddit': 50, 'rss': 24, 'youtube': 12, 'hn': 10}
```

Per-source breakdown: reddit 50, google news 80, google trends 0 (10 off-niche terms
dropped by the niche filter), youtube 12, industry rss 24 (but the specific "Perfect Daily
Grind" feed returned 0 entries with **HTTP 202** — accepted-but-empty, not an error), hacker
news 10 (20 off-niche stories dropped).

**TOTAL: 176 items, from 5 live sources (reddit, google news, youtube, rss, hn).** No
source raised an exception; the only anomaly is the Perfect Daily Grind RSS feed returning
202 with zero entries, and Google Trends returning zero items after niche filtering.

## Test 2 — Second niche (travel)

```
$ PYTHONPATH=/tmp/libs:. python3 -m studio.collector travel 2>&1

niches: travel

── travel ──
  [collector] reddit: 50 items
  [collector] google news: 80 items
  [collector] trends: dropped 10 off-niche trending terms
  [collector] google trends: 0 items
  [collector] youtube: 12 items
  [collector] rss Lonely Planet: 0 entries (http 200)
  [collector] industry rss: 8 items
  [collector] hn: dropped 19 off-niche stories
  [collector] hacker news: 11 items
  → 161 items

TOTAL: 161 raw items across 1 niches
by kind: {'news': 80, 'reddit': 50, 'youtube': 12, 'hn': 11, 'rss': 8}
```

**TOTAL: 161 items.** Same source mix works for a second, differently-configured niche —
this confirms the collector is genuinely multi-niche, not hardcoded to "coffee". Here the
"Lonely Planet" RSS feed returned 0 entries with HTTP 200 (empty feed, not blocked), and
industry rss overall only picked up 8 items (vs 24 for coffee) — niche-specific feed count,
not a failure.

## Test 3 — ffmpeg assembly (project's own `make_slideshow`)

```
$ PYTHONPATH=/tmp/libs:. python3 -c "... studio.factory.make_slideshow(paths, None, Path('/tmp/out')) ..."
SLIDESHOW_OK /tmp/out/slideshow.mp4 29046 bytes
```

Three 1024×1024 solid-colour JPEGs were generated with Pillow, the pip-installed
ffmpeg-7.0.2 binary was exposed on `PATH` as `ffmpeg`, and `studio.factory.make_slideshow`
was called directly (unmodified project code — zoompan + crossfade + 1080×1080 scale/crop
chain). It produced a real, playable MP4: **29,046 bytes**, no errors.

## Test 4 — Publish/generate endpoint reachability

```
telegram_getMe=401
fal_auth=405
pexels_auth=200
```

- **Telegram** (`api.telegram.org/bot123:FAKE/getMe`): **401** — reachable, fake token
  correctly rejected.
- **fal.run** (`queue.fal.run/fal-ai/flux/schnell`): **405** — reachable; the request
  reached the API (not blocked), but 405 (Method Not Allowed) because the probe command did
  a plain GET rather than the POST this queue endpoint requires. Auth was not actually
  exercised by this call — treat as "reachable, method mismatch," not a clean auth check.
- **Pexels** (`api.pexels.com/v1/search?query=coffee` with `Authorization: FAKE`): **200**,
  and the body contained real photo results. This is *not* an auth bypass in the app —
  response headers show `cf-cache-status: HIT` and `age: 42731` (~11.9h old), i.e.
  Cloudflare served a stale, publicly-cached copy of this exact URL from an earlier
  legitimately-authenticated request, keyed without regard to the `Authorization` header.
  A real invalid key would still fail once the CDN cache for that query expires or a
  different query is used. So: reachable, but this specific check did not actually prove
  auth is enforced — it proved the network path and CDN are live.

All three endpoints are reachable from this sandbox over HTTPS through the agent proxy. No
`000` (blocked) results.

## Verdict

1. **Does the real collector work here? How many items, from which sources?**
   Yes. `studio.collector` ran unmodified against live reddit, Google News, YouTube, RSS,
   and Hacker News endpoints and returned **176 items for `coffee`** and **161 items for
   `travel`**. Every source returned data or a well-formed empty result (HTTP 200/202) —
   none threw or timed out. Google Trends returned 0 items in both runs (all candidate terms
   were filtered as off-niche), which is a data/filtering outcome, not a network failure.

2. **Can ffmpeg be obtained via pip, and does the project's own video assembly then work?**
   Yes to both. `imageio-ffmpeg` installs a static ffmpeg 7.0.2 binary with no root/apt
   needed, and `studio.factory.make_slideshow` — called with zero modification — produced a
   valid 29KB MP4 from three generated stills. The only wiring needed is putting the binary
   on `PATH` as `ffmpeg` (one `shutil.copy`), which any startup/setup script for this
   environment could do automatically.

3. **Are the publish/generate endpoints reachable with auth headers?**
   Reachable, yes, for all three (Telegram, fal.run, Pexels) — no blocked (`000`) results.
   Telegram cleanly rejected a fake token (401), which is the clean confirmatory result.
   fal.run's 405 shows reachability but not a real auth check (wrong HTTP method for that
   endpoint). Pexels's 200 turned out to be a Cloudflare cache hit on the query URL rather
   than proof of a bypassed or accepted key — inconclusive on auth from this probe alone, but
   confirms the network path is live.

4. **FINAL: can this environment run the complete pipeline (collect → generate → assemble →
   publish) on a schedule?**
   **Collect and assemble are proven end-to-end in this sandbox using the project's own
   code.** ffmpeg is not preinstalled but is fully substitutable via `pip install
   imageio-ffmpeg` plus a one-line PATH shim — no root/apt required, so that is not a real
   blocker, just a one-time setup step. The publish leg (Telegram) and the generate leg
   (fal.run for images) are both network-reachable through the sandbox's HTTPS proxy; the
   remaining requirement is real credentials (bot token / API keys), which this probe
   deliberately did not test — that's a configuration step (secrets), not an environment
   capability gap. **No remaining technical blocker was found for running the full
   collect → generate → assemble → publish pipeline on a schedule in this environment**,
   provided: (a) the setup step installs `imageio-ffmpeg` and links it onto `PATH`, and
   (b) real credentials are supplied for Telegram/fal/Pexels via environment variables or
   secrets at run time (not tested here, and the fal/Pexels checks above should be re-run
   with a real key and the correct HTTP method before relying on them as an auth gate).
