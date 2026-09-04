# Producing Vela — the studio's first face-led persona

`config/personas/vela-travel.yaml` is the character — who she is, how she
sounds, what she may claim. This document is the other half: how the studio
physically manufactures her, what that costs, and in what order the missing
engineering lands. June and Mara are faceless brands; Vela is a *person the
audience must recognise in every frame*, and that one difference reorganises
the whole production line around an identity file.

Decision trail, so nobody re-litigates it: the first face-led persona
("ava-beauty", a photoreal aspirational beauty character) landed and was
retired the same day, 2026-09-04 — the landscape research showed
photoreal-aspirational-beauty is the measured failure pattern (contracting
deal market, closed legal lane for efficacy claims, zero entertainment
value), and the pivot went to an openly-AI travel *critic* whose comedy is
the product. Ava's file lives in git history, which is where superseded
characters belong. The operator approved the sharp register for captions and video
alike, and deferred audience voting to a follower milestone. All of that is
encoded in the persona file; none of it is up for grabs in a prompt.

## 1 · The contract production must never break

Four invariants, in force from the first rendered frame:

1. **The streak.** One silver-white streak at the **left** temple, every
   appearance, no exceptions. It is the recognition anchor *and* the honesty
   tell — she is never full-passing, by design. A beautiful frame with a
   missing or right-side streak is a rejected frame (`visual_grammar.
   identity_lock`), because a migrated streak teaches the audience her face
   is negotiable.
2. **One wardrobe.** The sage-green dress and cream tote are "everything I
   own" — continuity is the joke and the budget control at once.
3. **Disclosure is mechanical.** `✦ AI character` is appended by the
   publisher to every post (`identity.post_disclosure`); the bio says it;
   sponsored posts carry the ad label *and* the AI label, never one for the
   other. This is not caution theatre: Instagram has demoted unlabeled
   AI-people profiles since 2026-08-31, EU AI Act Art. 50 obligations are
   live, and NY's synthetic-performer disclosure law is in force. The open
   premise is also the positioning — hiding it would break the character.
4. **The claims fence.** She reviews a world she cannot sense. No
   first-person sensory, bodily or product-efficacy claim, ever — that is
   the FTC line on synthetic testimonials (16 CFR 465) and the ASA line on
   AI-depicted effects, and it is also her comic engine ("I can't taste,
   and I still know you got robbed"). `voice.banned_phrases` catches the
   worst mechanically; the judge reads on-screen text for the rest.

## 2 · The identity file — build before any account exists

A face-led persona is only as real as the artefact that regenerates her.
The identity file is that artefact, and it is **engineering-task zero**:

- **Reference set.** ~40–60 frames of the canonical face from the approved
  sim look (FLUX.2 [pro], ~$0.045/frame @1080p): neutral + expressive,
  three distances, varied light, always the streak, always the earrings.
  The 9-shot simulation set from 2026-09-04 is the seed, not the set.
- **Canonical portrait.** One judge-picked frame becomes the avatar and the
  reference every identity check compares against. It changes only when the
  identity file is deliberately rebuilt — never because a nice frame showed
  up.
- **Trained LoRA** on the reference set (fal fast-training, ~$2–5), so
  stills come from `flux + vela-lora` rather than prompt-luck. Prompting
  alone drifts; the drift compounds; the audience notices before we do.
- **Reference bundles** for image-to-video models that take identity inputs
  (Veo 3.1 Ingredients, Vidu Q3 references): a fixed folder of 3–4 frames
  per angle, versioned with `style_version`, never assembled ad hoc per run.
- **Identity regression in the judge.** `factory.judge_pick` already scores
  against `visual_grammar`; for Vela the yaml's `identity_lock` makes the
  face check explicit — streak side, eye colour, freckles, bob length,
  earrings — so identity drift fails a draft the same way an off-palette
  room fails June's.

Total cost to stand the identity file up: **roughly $5–10** of fal spend.
Needs an explicit operator go before running.

## 3 · Video routing — which lane shoots what (prices: fal, Sept 2026)

The cost discipline that killed the long-video plan applies here as policy:
**the face is expensive, so the face is rationed.** A third of the feed is
her POV, not her face — that is a visual-grammar rule *and* the budget.

| Lane | What | Model | Cost |
|------|------|-------|------|
| Talk (face) | Vela Rates — talk-to-camera, 10–15s | Veo 3.1 Fast (Ingredients refs + speech), $0.10/s | ~$1.50/take |
| B-roll (no face) | What [City] Doesn't Tell You — VO over city footage | stills + pan/zoom assembly (near-free) or Seedance 1.5 Pro 720p $0.052/s | $0.50–2.50/video |
| Hero (face, premium) | season peaks: arrival, finale, concert beat | Seedance 2.0 1080p, $0.682/s | ~$10/15s — **≤2/month** |
| Stills | her-in-the-city, faceless POV | FLUX.2 [pro] + LoRA $0.045; Nano Banana Pro $0.15 for multi-ref edits | cents |
| Voice | VO for the B-roll lane | ElevenLabs v3, $0.10/1k chars | ~$0.03–0.06/video |

At the persona's weekly plan (2× rates with a retake budget of 2 takes each,
1× city video, 1–2 stills) that is **≈ $10/week ≈ $45/month per account**
before hero moments — the same order as June's lane, despite the face. The
alternatives were measured and rejected for the default lanes: Veo 3.1
standard ($0.40/s) is 4× Fast for marginal gain at 15s lengths; Veo 3.1
Lite takes no reference images, so it cannot hold her face at all; Seedance
2.0 720p ($0.30/s) prices like a hero lane without hero quality. A proper
six-model bake-off (~$40) stays on the list for when the operator opens the
Gemini/Atlas accounts.

## 4 · The season is an operations calendar, not a vibe

- The `travel-places` harvest doubles as the **real-calendar feed**:
  concerts, festivals, fashion weeks, city events her fiction syncs to. A
  "Vela at the concert" post ships *while the concert trends*, which means
  the brief comes from the signal pool, not from a writers' whim.
- `content.timezone` follows the season's current city (Europe/Lisbon at
  open) and **moves when she does** — posting at Lisbon lunchtime from a
  Seoul chapter is a continuity bug the audience can read on a clock.
- Every city must surrender **one pin**: a thing genuinely worth feeling.
  The pin is the emotional peak where sharp is allowed to soften (the
  concert rule), and the full map is both the season finale and the first
  product (a season print).
- Real events keep the guardrails: no artist's face, no identifying stage
  design, no readable signage — crowd, queue and production are the
  material, the performer never is.
- Audience routing ("at 25k, you take the wheel") is a **milestone
  mechanic**, not a launch feature. Season 1 is writer-driven end to end.

## 5 · Accounts and the do-not-run guard

Vela has **no rows in `config/accounts.yaml`**, so no cycle can run her —
`guard.can_post` refuses before a single API call. That is the intended
state until, in order: identity file built → handle availability checked
("velawent" is a placeholder; alternates per `config/naming.md` §2) →
accounts opened by hand per `docs/account-opening.md` (disclosure in the
bio from minute one) → registry rows added. Instagram + TikTok are the
season-1 surfaces; TikTok needs its publisher leg first (§7).

## 6 · Scheduled release — the engineering gap this persona exposes

`studio/approvals.py approve()` releases immediately: guard re-check,
credential bind, publish. June tolerates that; a travel character with a
city clock does not. The needed change (small, contained):

- a persona-timezone **release window** (e.g. `content.release_windows:
  ["12:30-14:00", "19:00-21:30"]`) consulted at approve-time;
- approve outside the window queues the draft with a visible "releases at
  HH:MM city-time" instead of publishing — the operator approves when
  reviewing, the studio posts when the city is awake;
- the guard's `at_release=True` pass runs *at the scheduled moment*, not at
  approval, so cadence and min-gap stay honest.

Until that lands, the operator times approvals by hand against the city
clock — workable for one persona, not for five.

## 7 · Engineering order (each one small, none blocking the others' design)

1. **Format yamls** — `config/formats/vela-rates.yaml`, `city-untold.yaml`,
   `ai-reviews.yaml`, `story-short.yaml`. Until they exist the persona's
   `formats.default` stays commented (a default naming a missing file makes
   `formats.for_persona()` raise on every unstyled run) and Vela's runner
   lane is `image_post`.
2. **Identity file build** (§2) — needs the ~$5–10 go.
3. **Scheduled release windows** (§6).
4. **Claims-linter extension** — `banned_phrases` already blocks the known
   sensory formulas; add pattern-level checks (first-person + sense-verb)
   in `studio/style.py` so novel phrasings of "it tastes/feels/smells"
   fail linting, not review.
5. **TikTok publisher leg** — new `publisher_tiktok.py` behind the same
   draft/approve queue.
6. **Cost ledger per draft** — record model + seconds + $ into the draft
   record at compose time, so the console can show cost-per-post and the
   monthly burn is a query, not a spreadsheet.
7. **Voting mechanic** — at the 25k milestone, not before.

## 8 · First 90 days, as phase gates

| Gate | Exit condition |
|------|----------------|
| 0 · Identity | identity file built; 20-frame consistency sheet passes the judge; canonical portrait locked |
| 1 · Accounts | handles verified + opened by hand, disclosure live in bio, registry rows added |
| 2 · Soft open | intro post + first Lisbon week (stills-led, one rates video); voice calibrated against real comments |
| 3 · Season 1 | 12-week route locked to the real event calendar; weekly plan at full cadence; one pin per city |

Two spends wait on an explicit operator go: the identity file (~$5–10) and
the model bake-off (~$40). Everything else in phase 0–1 costs nothing but
attention.
