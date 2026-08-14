# Opening a persona account — the operator's runbook

`docs/account-safety.md` explains why accounts die. This is the other half:
the exact sequence for bringing a new account into the world so it never
trips those wires. Opening accounts is permanently human work (handbook
touchpoint register) — this document is the checklist that human follows.

The posture, in one line: **we are a disclosed media studio operating
character brands, and every account must look like exactly that** — to the
platform, to the audience, and to a moderator reading it cold.

## 0 · Before you open anything

- **The persona exists first.** Name from `config/naming.md` §2, bible
  drafted, avatar generated and judge-picked. The account is the persona's
  home, not a reserved username waiting for a character.
- **One identity, yours.** Registration email is a real mailbox you control,
  one address per persona, never reused. Plain Gmail is fine for the first
  few personas; past ~5, creating Gmails hits Google's own bulk-signup
  throttles and the login sprawl becomes its own risk — switch to one cheap
  domain with free forwarding (`june@…`, `mara@…` all landing in your real
  inbox). Recovery phone is your real number. Password is unique, generated,
  stored in your password manager. **Never** a fake name, a rented number,
  or a bought aged account — the owner is real and reachable; only the
  character is fiction.
- **No VPN, no proxy, no anti-detect browser.** Open accounts from your own
  home connection and your own devices. Circumvention tooling is itself the
  farm signature; we have nothing to hide.
- **Check the pace.** `fleet.stagger_account_creation_days` (7) is the
  minimum spacing between account openings, fleet-wide. Opening day is
  deliberately boring: one account, not a batch. At scale this is the
  throttle: a handful of new accounts per month, not per day.

## 1 · Opening day

1. Open the account **in the platform's own app** on a phone, like a person.
2. Set the avatar, display name, and the disclosed-AI bio **immediately** —
   disclosure is on from the first minute, not retrofitted later. (A bio
   added after growth reads as a confession; one there from day zero reads
   as a premise.)
3. Turn on 2FA. The mundane way accounts die is theft, not moderation.
4. Follow a handful of genuinely relevant accounts in the niche. Browse.
   Behave like the person who just made an account, because you are one.
5. **Record it in `config/accounts.yaml` the same day**: handle, real
   `opened_at`, `status: warming` (or the platform's warm-up default),
   `publish_mode: approve`. The warm-up governor reads `opened_at`, so it
   must be true. Credentials go to `.env` / the password manager — the
   registry never holds secrets.

## 2 · The warm-up weeks

- **No API connection yet.** Professional/Creator conversion (Instagram) can
  happen early — creators do that on day one and it reads normally — but
  binding the Meta app and minting tokens waits until the account has a
  human history: 1–2 weeks, a few hand-made posts, real minutes in the app.
- **The human supplies the organic signal** (handbook §3): a few follows,
  an occasional genuine reply, a story, ten minutes here and there. We never
  automate engagement, so the human provides what broadcasting alone lacks.
- First posts are made by hand from the app, with the same disclosure line
  the pipeline would append. The pipeline's own cadence starts at 1/day
  after the gate opens; do not front-run it manually.

## 3 · Standing hygiene at fleet scale

- **Losses correlate.** Meta's Account Integrity policy allows disabling
  accounts "owned by the same person or entity as an account that has been
  disabled." One dead account can price in the others. This is why a
  suspension is handled by appeal (`docs/account-safety.md` §5) and never by
  opening a replacement — replacement-after-ban is ban evasion, the one
  pattern that spreads.
- **Don't wire the fleet together in public.** No early cross-follows,
  cross-promotion, or shared link trees between personas. Each brand stands
  alone; the shared owner is a legal/business fact, not a growth tactic.
- **Same-moment publishing is a signature.** The scheduler jitters; never
  defeat that by hand-posting the fleet in one sitting.
- **Never repurpose or rename** an existing account into a new persona, and
  never buy accounts. Both are the strongest correlated-loss stories in the
  incident log.
- **When a platform asks for identity, answer truthfully** (business
  verification, "is this a bot" prompts, appeal forms). The studio's answers
  are always the same: yes, AI-generated content; yes, disclosed; yes, one
  operator; here is the entity.

## 4 · Platform notes

- **Instagram**: Creator account, category set, 2FA on. API via "Instagram
  API with Instagram login" — no App Review for your own accounts; one Meta
  app serves the whole fleet with per-persona tokens
  (`INSTAGRAM_ACCESS_TOKEN__<PERSONA>`). The publishing cap is a live
  number, not a constant — query `content_publishing_limit` (the preflight
  prints it; June's account reports 100/day as of 2026-08).
  **Turn on Instagram's native "AI creator" account label** the moment the
  option appears in profile settings (rolling out through 2026): it is our
  exact posture in platform-native form, it replaces per-post nagging with
  an account-level fact, and a voluntarily self-labeled AI creator is the
  strongest possible answer to any future "deceptive automation" review.
  Note Instagram's late-2025 ranking change: content that looks templated
  or mass-automated is deprioritized in distribution — the judge and the
  voice bible are not luxuries, they are reach.
- **Telegram**: channel + bot from @BotFather; the easy one. Channel handle
  follows naming §2; disclosure in the channel description.
- **Bluesky**: the strictest moderator of bot-adjacent accounts we have met
  (see the Mara incident). Warm-up is longest here (48h silence, then 1/day)
  and priority is lowest — open Bluesky legs last, if at all.
- **YouTube / TikTok**: see `docs/account-safety.md` §3c/§3d before opening;
  TikTok publishes via native scheduling only, by design.

## 5 · Verification log

Load-bearing claims in this document checked against primary sources on
**2026-08-14**:

- Meta Account Integrity policy (transparency.meta.com) currently lists, as
  grounds for restricting further assets, accounts *"Owned by the same
  person or entity as an account that has been disabled"* — the correlated-
  loss rule is live policy, verbatim. The same page reserves scrutiny for
  accounts created *"through automated means, such as scripting (unless the
  scripting activity occurs through authorized routes…)"* — hand-opened
  accounts publishing via the official API is the authorized shape.
- Instagram's "AI creator" account label and "AI Info" content labels exist
  and are voluntary as of mid-2026; Meta also auto-labels detected AI media.
  Our mechanical per-post disclosure remains, and the native label is added
  on top when available.
- The API publishing cap is per-account and served live by
  `content_publishing_limit` (observed: 100/24h on our account, up from the
  historical 25) — trust the endpoint, not blog posts.
- Meta retired its OWN first-party AI character accounts in 2025 after user
  backlash; user-run, clearly labeled AI creators are a recognized category
  (hence the label). The lesson is about audience trust, not permission:
  undisclosed or low-effort AI personas are what burned.

When acting on anything in this file months from now, re-verify against the
platform's own pages first — policies moved three times while this studio
was being built.

## 6 · The cadence at 100

The end-state fleet is reached the same way the second account was: one
deliberate opening at a time, spaced by the stagger rule, each with its own
warm-up, each recorded truthfully. There is no fast path — the fast paths
are the farm signatures. Practically this means the fleet grows by roughly
5–10 accounts a month, and that pace is a feature: it matches how fast the
studio can actually verify that new personas earn their keep.
