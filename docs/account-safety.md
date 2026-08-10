# Account safety — why accounts die, and how this studio avoids it

Losing a grown account is the most expensive failure this project can suffer.
Everything else — a failed harvest, a bad render, a missed cadence — is
recoverable in hours. An account with real followers is not.

This document is the standing playbook: what happened to the first account,
what the platforms actually enforce, and the rules every new account follows.
It is written from primary sources (platform guidelines, protocol docs,
maintainer statements) with the weak evidence marked as weak, because acting
on folklore is how people get banned twice.

---

## 1 · The incident

`sentronis.bsky.social` was taken down by Bluesky within roughly a day or two
of being provisioned as Mara. The platform does not disclose reasons, so the
following separates what is documented in this repository from what is
inference.

**Documented in the code, from an earlier incident:** `studio/guard.py` calls
itself "the suspension postmortem in one module", and `studio/publisher.py`
records that *"repeated logins from scripts were part of the pattern that got
the account flagged"*. The entire guard layer — warm-up curve, cadence caps,
minimum gaps, jitter, caption dedupe — exists because the account had already
been flagged once before the takedown.

**Measured, and it clears us of the obvious suspicion:** volume was never the
problem. Bluesky's published ceilings are 5,000 points/hour and 35,000/day
(a create costs 3 points → ~1,666 records/hour), plus 3,000 API requests per
5 minutes per IP. This studio posts at most twice a day and reads one public
profile a minute. We ran three orders of magnitude below every limit. "We
queried too much" is not what happened.

**The likeliest cause, and it is about identity, not volume.** An AT Protocol
maintainer has confirmed publicly that anti-spam detection runs *separately*
from the published rate limits: accounts well inside every documented limit
are suspended for the *shape* of their behaviour. Against that, this account
presented an unusually poor shape on day one:

1. **It was a repurposed account, not a persona account.** The handle
   `sentronis.bsky.social` shares nothing with the character it began
   presenting as ("Mara ☕"). An established handle that abruptly acquires a
   new name, a new avatar, a new bio and an automated posting schedule is
   indistinguishable from a compromised or resold account — and Bluesky's
   Account Authenticity rule names "identity churning" explicitly. Notably
   `config/naming.md` §2 already prescribed organic, persona-matching handles
   (`marabrews`-style); the Bluesky leg did not follow it, while the Telegram
   leg (`marabrews`) did — and Telegram is alive.
2. **It was pure broadcast from birth.** Zero followers, zero replies, zero
   interaction of any kind, publishing generated images on a schedule. That is
   the canonical silhouette of a content farm. (Our refusal to automate
   engagement is correct and stays — see §3 — but it means the *human* must
   supply the organic signal.)
3. **Repeated authentication.** `createSession` is limited to 30 per 5 minutes
   and 300/day — far below posting limits — and repeated logins are precisely
   the pattern the earlier flagging was attributed to. The session file is
   correctly gitignored as a credential, so every fresh clone or new machine
   re-authenticates from scratch.

**What we cannot reconstruct, and why:** the lineage database was
machine-local and was lost in a machine change, so the exact publish history
before the takedown is gone. This is itself the argument for the git-backed
metrics ledger (`data/metrics/`): platform state that matters must survive the
machine it was observed from.

---

## 2 · What Bluesky actually enforces

Sources: [Community Guidelines](https://bsky.social/about/support/community-guidelines)
(effective 15 Oct 2025), [Terms of Service](https://bsky.social/about/support/tos)
(14 Aug 2025), [rate limits](https://docs.bsky.app/docs/advanced-guides/rate-limits),
[bot template](https://docs.bsky.app/docs/starter-templates/bots),
[2025 transparency report](https://bsky.social/about/blog/01-29-2026-transparency-report-2025),
[atproto discussion #4626](https://github.com/bluesky-social/atproto/discussions/4626).

- **Automation is not banned; misleading accounts and spam are.** The
  guidelines contain no bot category at all. Automation is judged through the
  spam and authenticity rules.
- **There is no AI-labelling requirement on Bluesky.** Synthetic media appears
  only in narrow harm contexts (CSAM, non-consensual imagery, animal abuse).
  Our disclosure is therefore voluntary — we keep it because it is our ethical
  stance, not because the platform demands it.
- **The `bot` self-label is the one affirmative signal available.** It is an
  AT Protocol global label value that accounts may self-apply, and the
  official bot template asks automated accounts to set it. `provision.py` now
  does. (Caveat: the client-side feature request for surfacing it is still
  open, so its user-visible effect is uncertain — we set it as a good-faith
  record, not for display.)
- **Never interact with anyone who has not tagged you first.** The bot
  template states this explicitly as a spam-avoidance requirement.
- **Enforcement is overwhelmingly automated and skews to account-level
  action.** 95.34% of 2025's 16.49M labels came from automated systems, and
  authenticity violations "result in nearly all takedowns at the account
  level" rather than the post level. False positives are real: a brand-new
  high-profile account was auto-suspended within minutes by the impersonation
  classifier in June 2025 before being restored.
- **Appeals have a hard two-week deadline.** Account suspensions are appealed
  **in the app**; post takedowns by email to `moderation@blueskyweb.xyz`.
  Appeals must include enough specific detail for a meaningful review; late or
  thin ones "may not be eligible for reconsideration". No overturn rate or
  turnaround is published. (Third-party blogs cite `appeal@bsky.app` and a
  Zendesk form — neither appears in any official page. Do not use them.)
- **Ban evasion is a Critical-tier, immediate-permanent-ban offence.**
  Creating a replacement account for a suspended one is explicitly prohibited,
  alongside "help others do so", and produced 14,659 permanent removals in
  2025. **A new Bluesky account for this persona is not an option while the
  old one is suspended** — the only legitimate path is appeal.

---

## 3 · Standing rules for this studio

These are enforced in code where they can be, and are process rules where they
cannot.

**Never repurpose an existing account into a persona.** A persona account is
opened for that persona, with a handle from `config/naming.md` §2 that matches
the character, and it never presents as anything else. This is the single
lesson with the strongest support from the incident.

**One account per persona per platform, opened deliberately.** Record it in
`config/accounts.yaml` with its real `opened_at` date and `status`. The
warm-up governor reads that date, so it must be true. `fleet.stagger_account_creation_days`
(7) applies when a second persona arrives.

**A non-active status blocks publishing, mechanically.** `guard.can_post()`
refuses any platform whose registry status is not `active`, ahead of every
other check — credentials are not permission. This is what stops a suspended
account from being retried into a permanent ban.

**Never automate engagement.** No follows, likes, replies, reposts — there is
no such code path in this repository, and there must not be one. Where a
platform allows interaction, it is a human doing it, from the app.

**The human supplies the organic signal.** Because we do not automate
engagement, a new account that only broadcasts looks like a farm. For the
first weeks the operator should use the account like a person would: follow a
handful of relevant accounts, reply where genuinely interested, post something
by hand. Ten minutes a week buys the signal automation cannot.

**Authenticate once, keep the session.** `publisher.login()` reuses a
persisted session and budgets full logins (5/hour, 20/day) — approaching that
budget means a crash loop or an unwritable session file, and it stops loudly
rather than hammering the endpoint that costs accounts.

**Warm up, always.** Bluesky: silent for 48h after provisioning, then 1/day
for a week, then 2/day. Jitter every scheduled publish (identical daily
clock-times are a cron signature). Never post from a fleet at the same moment
— simultaneity from shared infrastructure is the one trigger a maintainer has
confirmed on the record.

**Disclose, on every platform, in the platform's own mechanism.** Our text
disclosure is appended mechanically to every post; where the platform has its
own synthetic-media flag, set it too (YouTube's `containsSyntheticMedia` is
set programmatically in `studio/publisher_youtube.py`).

---

## 3b · Production rules — what we generate

Two rules govern every image and video the studio makes. They are enforced in
`studio/brain.py` (the brief prompt) and, for the first, mechanically in
`run.py` — because a prompt instruction is one layer and this repository's
standing rule is never to trust one layer.

**Generic subjects, never real ones.** Everything we publish is synthetic, so
it must depict a *kind* of thing, never a specific verifiable one: "a granite
alpine lake at sunrise", never "Lake Louise"; "a sunlit corner café", never a
named café. Never a real person, event, business or product. Signals name real
places constantly — that is the source's language, not ours; take the
aesthetic and leave the proper nouns.

The reason is not squeamishness. A fabricated depiction of a real subject is a
false statement that our AI disclosure does not cure: a viewer who searches
the place finds our picture is not it. Authenticity findings take accounts
down, not posts.

`brain.real_subject_leaks()` compares the brief's image prompts against the
proper nouns in the signal that produced them. A leak regenerates the brief
once with explicit feedback; a second leak fails the cycle rather than render
it. The detector reads region and period words (Western, US, August) and our
own category vocabulary as generic — source titles arrive in title case, so
"Nature Shapes Every Room" must not make "room" a banned subject. It is
deliberately biased toward false positives: an unnecessary regeneration costs
one model call, a missed leak costs an account.

**One post, one idea of its own.** The brief's `angle` is the actual product:
a specific editorial point of view someone could disagree with. Never a
caption that would sit under any image in the category; never a template with
the nouns swapped.

This is a monetization rule as much as an editorial one. YouTube renamed its
"repetitious content" policy to **inauthentic content** on 15 July 2025 and
broadened it to any channel built on mass-produced templates, recycled clips,
**slideshows with no narrative**, or scripts read verbatim. AI-assisted work
that adds original analysis and framing stays eligible; AI mass production
does not. Disclosure itself never costs eligibility — sameness does.

Consequently `run.py` refuses to send a stills slideshow to YouTube: the hero
clip is the only cut that carries a shot, so it is the only cut that goes
there. Slideshows live on Telegram and Bluesky.

### Where monetization is easier, and why

Difficulty tracks how much of the money flows through the platform's own
judgement:

| Path | Needs platform approval | Notes |
|---|---|---|
| Affiliate | No | No threshold, no review, works from day one on any surface |
| Brand deals | No — the brand pays | Needs an audience and a media kit, not platform blessing |
| Owned audience (Telegram, newsletter) | No gatekeeper at all | Why Telegram is the funnel's home |
| Instagram | Effectively none | Reels Play Bonus ended 31 Aug 2025 — there is no program left to be rejected from |
| TikTok | Partly | Sources conflict on whether AI content earns Creator Rewards; verify against TikTok's own docs before relying on it |
| YouTube | Most | Ad share is the platform's money, gated by the authenticity bar, high thresholds, manual review and a three-strike disclosure system |

For this studio that means YouTube is a reach and funnel surface, not a
revenue line.

## 4 · Opening a new account — checklist

Run through this before the account exists, not after.

1. **Pick the handle first**, per `config/naming.md` §2 — organic, matching the
   persona, never sequential or fleet-patterned. Check it is free on every
   platform the persona will use, so the identity stays consistent.
2. **Open it as a human, from a normal session.** Real signup, no automation
   in the loop, complete the profile (avatar, bio with disclosure, banner)
   before any automated post exists.
3. **Add it to `config/accounts.yaml`** with `opened_at` set to the real date
   and `status: active`.
4. **Leave it alone for the warm-up window** — the guard enforces silence, but
   this is also when the human should do the organic-signal work in §3.
5. **Set the platform's own AI/synthetic flag** and check the disclosure
   renders correctly in a real post before scheduling any.
6. **Only then enable automation**, at the lowest cadence the policy allows.

---

## 5 · If an account is suspended

1. **Stop publishing to it immediately** — set `status: suspended` in
   `config/accounts.yaml`. The guard then blocks it mechanically.
2. **Appeal within two weeks**, through the platform's own account-level
   channel (in-app for Bluesky). State plainly what the account is: a
   disclosed AI persona, posting original generated media on a schedule, with
   no engagement automation, at N posts/day. Specifics beat protest.
3. **Do not open a replacement account.** On Bluesky this is a Critical-tier
   permanent ban, and it can poison the persona's other legs.
4. **Record the outcome** in `config/accounts.yaml` and in this document, so
   the next decision is made from evidence rather than memory.
