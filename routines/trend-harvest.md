# Routine · Daily Trend Harvest

The shared intelligence layer, run on a schedule in the cloud so it keeps
working with every laptop closed.

**What it does:** collects across all launch-priority content categories, turns
the raw noise into typed and scored signals, and commits the result to the repo
as a signal pool. It also measures the fleet — public engagement for every
account in `config/accounts.yaml`, credentials never involved — and commits
that as the metrics ledger. Publishing is a separate concern — this routine
never posts.

**Why it is separate from posting:** collection is shared and posting is
per-account. One harvest feeds every persona subscribed to a category. Running
collection per account would pay N times for identical data and, worse, have N
accounts posting the same wave on the same day.

**Why the analysis happens inside the routine:** the routine *is* Claude, so
signal typing and scoring run on the operator's existing subscription. No API
key, no separate credential, no extra bill.

---

## Schedule

`0 4,13 * * *` UTC — 07:00 and 16:00 Europe/Istanbul. Twice daily gives every
account a fresh pool for a morning and an afternoon slot, and keeps signals
inside their expiry windows.

## Environment

Must run in an environment whose outbound network is open. This was measured,
not assumed: the default environment blocks every content source at the proxy,
while `calidef-world-reports` (`env_0171BcbjXXF3tdciZCmRsVPy`) reaches all of
them. See `docs/cloud-probe-v3.md` for the evidence.

`ffmpeg` is absent from the image but is not a blocker — `pip install
imageio-ffmpeg` ships a static binary, and the project's own video assembly was
verified working against it in that environment.

## Output contract

Each run writes:

    data/signals/<category>/latest.json     the current pool for that category
    data/signals/<category>/<timestamp>.json  an immutable snapshot
    data/signals/index.json                 run metadata: counts, per-source yield
    data/metrics/<platform>--<handle>/latest.json   current engagement snapshot
    data/metrics/<platform>--<handle>/history.jsonl append-only trend ledger

Consumers (`run.py`, the ops console) read `latest.json` for their category;
the console's Performance view reads the metrics ledger. Nothing else in the
repo is touched, so a harvest can never break publishing. Metrics history
lives in git for the same reason the pools do: it survives machine changes
and works on any fresh clone.

## The prompt

The routine's instructions live in `routines/trend-harvest.prompt.md`, kept in
version control so a change to how signals are judged is reviewable like any
other change.
