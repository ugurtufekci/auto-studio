# Cloud Probe v2 — Layer-by-Layer Network & Tooling Audit

Date: 2026-08-07
Environment: Claude Code on the web, remote execution container (session-scoped; a different environment instance than the earlier probe).

Purpose: The earlier probe tested network access **only via Bash/curl** and concluded "everything is blocked." That conclusion was incomplete — it never tried the WebFetch/WebSearch tools, which route through a different path (an agent-side fetch proxy) than Bash's outbound network. This probe tests each layer independently and does not generalize results from one layer to another.

---

## Layer A — Bash/curl (sandbox outbound network)

Command pattern: `curl -s -o /dev/null -w '%{http_code}' --max-time 12 <URL>`

| # | URL | HTTP code | Notes |
|---|-----|-----------|-------|
| A1 | `https://www.reddit.com/r/espresso/hot.rss` | **403** | Reachable, but Reddit blocks the request (bot/UA filtering) |
| A2 | `https://news.google.com/rss/search?q=coffee` | **302** | Reachable — redirect, not blocked |
| A3 | `https://api.telegram.org/` | **302** | Reachable — Telegram's landing redirect |
| A4 | `https://api.pexels.com/v1/search?query=coffee` | **200** | Reachable (would 401 without an API key, but the point is it's not `000`) |
| A5 | `https://fal.run/` | **404** | Reachable — root path just isn't a valid route |

**Verdict for Layer A:** Bash/curl outbound network is **not** blocked in this environment. All five targets returned real HTTP status codes, none were `000`. The earlier probe's "everything blocked" conclusion does not hold in this run — either the earlier environment instance genuinely differed, or the earlier test/targets were flawed. This probe only vouches for what it directly observed above.

---

## Layer B — WebFetch tool

| # | URL | Result | Raw or summarized? |
|---|-----|--------|---------------------|
| B1 | `reddit.com/r/espresso+Coffee+cafe/hot.rss` | **Failed** — `Claude Code is unable to fetch from www.reddit.com` | N/A — tool-level block on this host |
| B2 | `news.google.com/rss/search?q=specialty+coffee` | Success, 15 items shown (feed noted as "100+ total items; truncated for brevity") | Raw XML, titles quoted verbatim |
| B3 | `sprudge.com/feed` | Success, 10 items shown | Raw XML |
| B4 | YouTube channel RSS (`UCMb0O2CdPBNi-QqPk5T3gsQ`) | Success, 15 items shown | Raw XML |
| B5 | HN Algolia `search_by_date?query=coffee&tags=story` | Success, 20 hits shown with titles + points | Raw JSON |

Evidence — actual titles received:
- B2 (Google News RSS): *"Why specialty coffee is now gaining wider recognition outside the industry - Perfect Daily Grind"*; *"The coffee industry is no longer looking West - Coffee Intelligence"*
- B3 (Sprudge RSS): *"Ivan Solis Of Recuerdos Cafe: The Sprudge Twenty Interview"*; *"The American Heart Association Says Five Cups Of Coffee A Day Is Fine"*
- B5 (HN JSON): *"Less Coffee, Better Sleep" — 54 points*; *"Show HN: Advanced Coffee Search Covering Over 17,000 coffees" — 4 points*

**Important clarification on raw vs. summarized:** WebFetch runs the fetched page through a small model before returning it, so what comes back is that model's transcription of the feed (titles/points listed out), not the byte-for-byte XML/JSON. In this run it faithfully reproduced item titles, ordering, and (for B5) points, and is good enough to parse/score programmatically by re-prompting for structured fields — but it is not literally piping raw bytes to you the way `curl` would. Treat it as "high-fidelity extraction," not "raw feed passthrough."

**Reddit-specific finding:** Layer A reached `reddit.com` (403, but reachable at the TCP/TLS level). Layer B's WebFetch tool refused `www.reddit.com` outright at the tool layer, independent of what curl saw. This confirms the two layers are genuinely different network paths with different blocklists — consistent with this probe's premise, and a concrete reason not to assume A and B behave the same per-host.

---

## Layer C — WebSearch tool

Query: `specialty coffee trend august 2026`

Result: **Returned results.** 9 results listed with titles + URLs, followed by a synthesized summary.

Evidence — actual result titles:
- *"New Release: 2026 National Coffee Data Trends Specialty Coffee Report — Specialty Coffee Association"*
- *"Top 14 Coffee Trends of 2026 – Glimpse"*

**Verdict for Layer C:** WebSearch works and returns real, dated (2026) results.

---

## Layer D — Write operations (the publishing question)

**D1 — Can WebFetch issue a POST with a JSON/multipart body and custom headers?**
**No.** The WebFetch tool's schema takes only `url` (string) and `prompt` (string) — no method, headers, or body parameters exist. It is a GET-and-summarize tool by design; there is no way to make it perform an authenticated POST. This is a hard capability limit of the tool itself, not a network block.

**D2 — Bash POST test:**
```
curl -s -o /dev/null -w '%{http_code}' --max-time 12 -X POST \
  https://api.telegram.org/bot123456:FAKE/getMe
```
Result: **401** (not `000`).

A `401` means the request reached Telegram's API and was rejected only for the fake bot token — the network path is open and POST is not blocked. This is a strong positive signal: publishing to Telegram via Bash/curl (Layer A) with a **real** bot token would very likely work.

---

## Layer E — Tooling

```
$ python3 --version
Python 3.11.15

$ which ffmpeg
(no output — not found)

$ ffmpeg -version
bash: ffmpeg: command not found

$ which git
/usr/bin/git

$ nproc
4
```

**ffmpeg is NOT present in this environment.** This was re-checked directly in this run (not assumed from the earlier probe, which used a different environment id) — confirmed absent here too. Python 3.11, git, and 4 CPU cores are available.

---

## Verdict

**1. Can we COLLECT trend data in this environment? Via which layer, and is it parseable enough to score programmatically?**
Yes. Both Layer A (curl, e.g. Google News RSS at A2 returned 302→reachable, Pexels 200) and Layer B (WebFetch) can collect trend data. WebFetch is the more practical layer for RSS/JSON sources like Google News RSS, Sprudge, YouTube channel feeds, and the HN Algolia API — it returned clean, ordered item titles (and points, for HN) that are usable as structured signal for scoring. Layer C (WebSearch) supplements this with live, dated web-search results for broader trend context. Caveat: Reddit is blocked at the WebFetch tool layer specifically (B1 failed) even though curl could reach it (A1, 403). A real pipeline should not rely on Reddit via WebFetch, and should expect to need Reddit's own API/auth via curl if that source matters, or drop it in favor of the RSS/JSON sources that worked cleanly (Google News, Sprudge, YouTube, HN).

**2. Can we PUBLISH (HTTP POST to Telegram) in this environment? Via which layer, or not at all?**
Only via Layer A (Bash/curl). D2 got a `401` (reachable, bad fake token) rather than `000` (blocked) — with a real bot token this would work. WebFetch (Layer B) cannot do this at all: it has no POST/header/body capability (D1), so it structurally cannot publish regardless of network access. **Publishing must go through Bash/curl**, not WebFetch.

**3. Can we GENERATE media (fal.ai API calls) and ASSEMBLE video (ffmpeg)?**
- fal.ai: Layer A reached `https://fal.run/` (404 on root, which is expected — it's not a valid route, but the host is reachable and not `000`). A real fal.ai call would need Bash/curl with a proper endpoint path, method, and auth header, same as Telegram — should work the same way D2 suggests, though this probe only tested the bare root path and no authenticated endpoint.
- ffmpeg: **Not available** in this environment (Layer E — confirmed absent on direct re-check, not assumed). Video assembly cannot run here as-is; it would need either installing ffmpeg (if the environment allows package installation) or moving that step to an environment/container that has it.

**4. Overall: which parts of a collect → generate → publish pipeline can run here, and which cannot?**
- **Collect:** ✅ Can run here — WebFetch (RSS/JSON feeds) + WebSearch (broader trend queries) + Bash/curl where needed. Avoid Reddit via WebFetch specifically.
- **Generate (fal.ai calls):** 🟡 Likely can run via Bash/curl (network path is open per A5/D2-style reachability), but this probe did not make an authenticated fal.ai call — only confirmed the host is reachable and unblocked. Needs a real API-key test to confirm.
- **Assemble (ffmpeg):** ❌ Cannot run here — ffmpeg is not installed in this environment.
- **Publish (Telegram POST):** ✅ Can run here via Bash/curl — D2's `401` (not `000`) indicates the path is open and a real token would succeed. Must use Bash/curl, not WebFetch, since WebFetch cannot POST.

**Bottom line:** This environment can do collect + publish end-to-end today, and generate is very likely reachable pending an authenticated test, but video assembly (ffmpeg) is the one hard blocker — it isn't a network restriction, it's a missing binary, and needs to be solved separately (install it, or run that one step in a different environment).
