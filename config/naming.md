# Naming convention — internal systematic, public organic

Two separate namespaces. Mixing them is how fleets get detected in bulk.

## 1. Internal ID (systematic, never public)

Used in file paths, DB rows, config filenames, logs, credential keys.

    <name>-<niche>[-<locale>]

    mara-coffee          juno-travel         nova-fitness-tr

Rules:
- lowercase, hyphen-separated, stable **forever** — never changes even if the
  public handle changes (rebrand, reinstatement under a new handle)
- one config file per persona: `personas/mara-coffee.yaml`
- credential env keys derive from it:
  `TG_CHANNEL__MARA_COFFEE`, `BSKY_HANDLE__MARA_COFFEE`

## 2. Public identity (organic, deliberately inconsistent)

Every handle must look like an individual picked it. **Vary the structure** —
rotate between these shapes so no single pattern covers the fleet:

| Shape | Examples |
|---|---|
| name + verb | `marabrews`, `junoroams`, `novalifts` |
| concept phrase | `slowmorningsclub`, `thirdplacediary`, `quietmileage` |
| short / clipped | `cremaco`, `oatandash`, `duskmiles` |
| object-led | `thegrindnotes`, `windowseatcoffee` |

Hard rules:
- **Never** sequential numbers (`mara1`, `mara2`) or padded ids (`persona07`)
- **Never** the same suffix across the fleet — no fleet-wide `_daily`,
  `_official`, `_hq`, `_ai`, `_bot` on channels
- Vary length (7–20 chars), vary underscore use (most without, a few with)
- Vary display-name style: some with one emoji, some none, some with a tagline,
  some bare. Do not template the display name.
- Vary bio wording persona to persona — the AI disclosure must always be
  present, but write it differently each time. Identical bio text across
  accounts is a stronger fingerprint than an identical handle.
- Numbers only if they read naturally (`oat36`, `route66diner`), never as an index

## 3. Registry

`personas` table in `store/studio.db` is the single source of truth linking the
two namespaces:

    id | internal_id  | platform | handle              | niche         | created_at

Keep `internal_id` unique; `handle` may change over a persona's life.

## 4. Telegram specifics

- **One bot for the whole fleet** — the bot is infrastructure, invisible to
  viewers (channel posts are attributed to the channel, not the bot). Bot
  username must end in `bot` (Telegram requirement): `@autoStudioPublisherBot`
- **One channel per persona** — this is the public identity; name it per §2
- Add the single bot as admin of every channel
- Hide the admin list in channel settings so the operator account isn't exposed
