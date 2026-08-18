#!/usr/bin/env python
"""autoStudio — one fully autonomous cycle: read the signal pool → brief →
generate → publish → lineage. The S0 spine, end to end, no human.

Collection is shared, publishing is per-account: the cloud trend-harvest
routine (routines/trend-harvest.md) collects and scores every category twice a
day and commits the result to data/signals/<category>/latest.json. A cycle
here reads its persona's pool instead of re-collecting — the food-drink
account and the travel-places account draw different signals from the same
harvest.

  python run.py                 # full cycle, publishes to Bluesky
  python run.py --dry-run       # everything except the publish call
  python run.py --format slideshow_video
  python run.py --hero          # true text-to-video post (Wan) instead
  python run.py --live-collect  # gather + score in-process (no pool needed)

Every stage reports into the events table — the ops dashboard
(dashboard/serve.py) renders the run live.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from studio import (  # noqa: E402
    adapt,
    brain,
    collector,
    credentials,
    deliver,
    draftpool,
    factory,
    formats,
    guard,
    learning,
    persona,
    pool,
    signals,
    store,
    style,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def persona_category(persona_id: str) -> str:
    """The category whose signal pool this persona draws from. Falls back to the
    first configured category so a missing key never stops a cycle."""
    try:
        n = persona.category_of(persona_id)
        if n and n in collector.available_categories():
            return n
    except Exception:
        pass
    return collector.available_categories()[0]


def pick_format(forced: str | None, persona_id: str | None = None) -> str:
    """The format for this cycle, drawn from the persona's own mix.

    The mix is not decoration: a persona's formats are policy choices —
    June's slideshow is Instagram-only and silent (see her yaml), because
    YouTube names image slideshows as inauthentic and TikTok excludes them
    from originality. Before this read the persona, the clock alone decided —
    and after 14:00 every persona made slideshows."""
    if forced and forced != "auto":
        return forced
    mix = [m for m in ((persona.load(persona_id).get("content") or {}).get("mix") or [])
           if m in ("image_post", "slideshow_video")]
    if not mix:
        return "image_post"
    # deterministic by clock so scheduled runs rotate rather than repeat
    return mix[0] if datetime.now().hour < 14 else mix[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no publish call")
    ap.add_argument("--format", default="auto",
                    choices=["auto", "image_post", "slideshow_video"])
    ap.add_argument("--hero", action="store_true",
                    help="true text-to-video post via Wan (demo weapon)")
    ap.add_argument("--now", action="store_true",
                    help="skip the anti-pattern jitter delay (interactive/demo runs)")
    ap.add_argument("--persona", default="",
                    help="which persona speaks this cycle (config/personas/*.yaml). "
                         "Defaults to $PERSONA, else the first configured one.")
    ap.add_argument("--category", default="",
                    help="which category's signal pool to read (default: the "
                         "persona's own). Comma-separate for several.")
    ap.add_argument("--style", default="",
                    help="which named video style to shoot "
                         "(config/formats/*.yaml). Defaults to the persona's "
                         "own default style.")
    ap.add_argument("--secs-per-frame", type=float, default=0.0,
                    help="seconds each slideshow frame holds — matching a "
                         "reference reel's pace is a re-assembly, not a "
                         "re-render, so this is cheap to change")
    ap.add_argument("--concept", default="",
                    help="the operator's own brief, in their words — a "
                         "reference concept to work from INSTEAD of today's "
                         "trend signal. The persona's rules still apply; the "
                         "trend pool is not read at all.")
    ap.add_argument("--frames-only", action="store_true",
                    help="stop after the keyframes, before a single "
                         "transition is bought. Iterating on a morph style "
                         "costs ~$0.15 this way against ~$1.15 for the whole "
                         "reel, and every problem worth finding — the wrong "
                         "before-room, styles that repeat, an edit that did "
                         "not take — is visible in the frames.")
    ap.add_argument("--from-run", default="",
                    help="build the video from an existing run's keyframes "
                         "(assets/run-…) instead of rendering new ones. The "
                         "companion to --frames-only: iterate the frames for "
                         "~$0.15, then buy the transitions ONCE against the "
                         "set you actually approved.")
    ap.add_argument("--live-collect", action="store_true",
                    help="collect + score in-process instead of reading the "
                         "shared pool (for a category the harvest doesn't cover)")
    args = ap.parse_args()

    # One cycle speaks as exactly one persona. Resolve it once here and pass it
    # down — the disclosure line is per-persona, so an implicit lookup deep in
    # an adapter is how one character ends up publishing under another's name.
    persona_id = args.persona.strip() or persona.default_id()
    who = persona.load(persona_id)
    log(f"persona: {who['identity']['name']} ({persona_id}) "
        f"→ {persona.category_of(persona_id)}")

    # This persona's suffixed keys (BLUESKY_HANDLE__JUNE, …) take over the
    # bare names before anything reads a credential — see studio/credentials.py
    applied = credentials.overlay(persona_id)
    if applied:
        log(f"credentials: per-persona keys applied for "
            f"{', '.join(sorted(set(applied)))}")

    con = store.connect()

    # ── 0 · guardrails (before anything costs money or touches accounts) ──
    # Targets come from the fleet registry: a persona publishes where it has
    # accounts, nowhere else. PLATFORMS narrows a run to a subset; it can no
    # longer invent a target the registry doesn't know about.
    legs = guard.registry_platforms(persona_id)
    wanted = [p.strip() for p in os.environ.get("PLATFORMS", "").split(",")
              if p.strip()]
    requested = [p for p in legs if not wanted or p in wanted]
    if not requested:
        detail = (f"PLATFORMS={','.join(wanted)} excludes every registry "
                  f"account of '{persona_id}' ({', '.join(legs) or 'none'})"
                  if wanted else
                  f"persona '{persona_id}' has no accounts in "
                  f"config/accounts.yaml")
        log(f"nothing to publish — {detail}")
        return 0
    targets = requested
    if not args.dry_run:
        targets, blocked = guard.allowed_platforms(con, requested, persona_id)
        for reason in blocked:
            log(f"guardrail blocked → {reason}")
        if not targets:
            note = "; ".join(blocked) or "no platforms configured"
            log("all platforms blocked by guardrails — cycle skipped")
            cid = store.start_cycle(con, 0)
            store.log_event(con, cid, "publish", "failed", f"guardrail: {note}")
            store.finish_cycle(con, cid, "failed", f"guardrail: {note}")
            return 0
        log(f"guardrail: publishing to {', '.join(targets)}")
        if not args.now:
            delay = guard.jitter_minutes(guard.load_policy(targets[0]))
            if delay:
                log(f"jitter: waiting {delay} min before this run "
                    f"(identical daily clock-times are a bot signature)")
                time.sleep(delay * 60)

    run_dir = ASSETS_DIR / time.strftime("run-%Y%m%d-%H%M%S")
    cycle_id = store.start_cycle(con, 0)
    ev = lambda stage, status, detail="": store.log_event(con, cycle_id, stage, status, detail)  # noqa: E731

    try:
        # ── 1+2 · signals — shared pool by default, in-process with --live-collect ──
        categories = ([n.strip() for n in args.category.split(",") if n.strip()]
                  or [persona_category(persona_id)])
        if args.concept.strip():
            # The operator hands the studio a concept — usually "make one like
            # this", pointing at a post that already worked. It takes the
            # signal's seat entirely: no pool is read, and nothing about the
            # day's trends gets to steer the subject. Their words are the
            # brief's subject; the persona's rules still shape how it is made.
            concept = args.concept.strip()
            log("operator concept — the trend pool is not read for this run")
            ev("collect", "done", "operator concept — no pool read")
            # scored 1.0 across the board: an operator instruction does not
            # compete with trends, it replaces them — and the audit row has
            # to record it like any other signal the studio acted on
            sigs = [{"topic": concept[:120], "signal_type": "operator",
                     "summary": concept, "score": 1.0, "velocity": 1.0,
                     "niche_fit": 1.0, "producibility": 1.0,
                     "expiry_hours": 24, "source_count": 1,
                     "exemplar_urls": [],
                     "why_now": "the operator asked for this directly — it is "
                                "not a trend to interpret but an instruction "
                                "to follow"}]
        elif args.live_collect:
            # legacy path: gather from public endpoints and score right here
            log(f"live-collecting trends for: {', '.join(categories)}")
            ev("collect", "running", f"live collect: {', '.join(categories)}")
            raw = collector.collect_all(
                categories,
                on_progress=lambda src, n: ev("collect", "progress", f"{src}: {n} items"))
            store.update_cycle_raw(con, cycle_id, len(raw))
            ev("collect", "done", f"{len(raw)} raw items")
            log(f"cycle #{cycle_id}: {len(raw)} raw items")
            if len(raw) < 5:
                raise RuntimeError("too few raw items collected")
            log("normalizing into typed signals…")
            ev("signals", "running", f"LLM typing {len(raw)} items")
            sigs = signals.normalize(raw)
            if not sigs:
                raise RuntimeError("no signals survived the gates")
        else:
            # shared pool: the harvest routine already collected and scored
            log(f"reading the shared signal pool: {', '.join(categories)}")
            ev("collect", "running", f"pool read: {', '.join(categories)}")
            sigs, pools = pool.read_signals(categories)
            for p in pools:
                line = (f"{p['category']}: {p['kept']} fresh signals "
                        f"(harvested {p['age_hours']}h ago"
                        + (f", {p['expired']} expired dropped" if p["expired"] else "")
                        + ")")
                ev("collect", "progress", line)
                log(f"  {line}")
                if p["stale"]:
                    warn = (f"{p['category']} pool is {p['age_hours']}h old — "
                            "has the trend-harvest routine stopped?")
                    ev("collect", "progress", f"STALE POOL: {warn}")
                    log(f"  WARNING: {warn}")
            store.update_cycle_raw(con, cycle_id,
                                   sum(p["raw_item_count"] for p in pools))
            ev("collect", "done",
               f"pool read — {sum(p['raw_item_count'] for p in pools)} raw items "
               f"upstream, {len(sigs)} fresh signals")
            log(f"cycle #{cycle_id}: {len(sigs)} fresh signals from the pool")
            if not sigs:
                raise RuntimeError(
                    f"signal pool empty for {', '.join(categories)} — every signal "
                    "expired or the harvest wrote none. Run the trend-harvest "
                    "routine, or use --live-collect.")
        sig_ids = store.save_signals(con, cycle_id, sigs)
        for s in sigs[:5]:
            log(f"  {s['score']:.2f} [{s['signal_type']}] {s['topic']}")
        ev("signals", "done", f"{len(sigs)} signals · top: “{sigs[0]['topic']}” ({sigs[0]['score']:.2f})")

        # ── 3 · brief ──────────────────────────────────────────
        top, top_id = sigs[0], sig_ids[0]
        store.mark_chosen_signal(con, top_id)
        fmt = "image_post" if args.hero else pick_format(args.format, persona_id)
        # a named style (config/formats/*.yaml) decides what kind of video
        # this is; `look` is its delivery settings, persona keys as fallback
        # With no --style the studio does not simply repeat its habit: it
        # shoots whichever adopted style the numbers favour, keeping a share
        # of runs curious so a style that lost early still gets its turn.
        wanted = args.style
        if not wanted and fmt == "slideshow_video":
            adopted = [str(x) for x in ((who.get("content") or {})
                                        .get("formats") or {}).get("allowed") or []]
            # a style may be adopted and still stay out of the unattended
            # rotation — the morph reel costs about seven times a cut-based
            # one, and that is the operator's call to make per run, not a
            # thing a nightly cycle should wander into
            adopted = [x for x in adopted
                       if formats.load(x).get("auto_rotate", True)]
            if len(adopted) > 1:
                handle = (guard.registry_account("instagram", persona_id)
                          or {}).get("handle", "")
                wanted, why = learning.choose(persona_id, handle, adopted)
                log(f"style choice: {why}")
        video_style = (formats.for_persona(persona_id, wanted)
                       if fmt == "slideshow_video" else None)
        look = formats.settings(video_style, persona_id)
        if video_style:
            log(f"style: {video_style['name']} ({video_style['id']}) — "
                f"{video_style.get('tagline', '')}")
            ev("brief", "progress", f"style: {video_style['name']}")
        log(f"chosen signal: “{top['topic']}” → format: {'hero_clip' if args.hero else fmt}")
        ev("brief", "running", f"writing brief for “{top['topic']}”")
        # --from-run reuses the frames, so it must reuse the BRIEF that made
        # them: a fresh one would put "Art Deco" over the Moroccan room, and
        # would spend two minutes of model time rewriting something already
        # approved. Every gate below belongs to writing a brief, not to
        # reading one back.
        saved = (ASSETS_DIR / Path(args.from_run).name / "brief.json"
                 if args.from_run else None)
        if saved and saved.exists():
            brief = json.loads(saved.read_text(encoding="utf-8"))
            log(f"reusing the brief from {saved.parent.name} — the frames were "
                f"made from it, so their names must come from it too")
            ev("brief", "done", "reused with the keyframes")
        else:
            brief = brain.make_brief(top, fmt, persona_id=persona_id, style=video_style)
            if guard.is_duplicate_caption(con, brief["caption"]):
                ev("brief", "progress", "caption duplicated a recent post — regenerating")
                log("caption too similar to a recent post — regenerating once")
                brief = brain.make_brief(top, fmt, avoid_captions=[brief["caption"]],
                                         persona_id=persona_id)
            # Signals name real places constantly; the imagery must not. A synthetic
            # picture of a named real subject is a fabrication the disclosure does
            # not cure, so a leak costs this cycle rather than the account.
            # The voice contract is enforced like the real-subject rule: one
            # regeneration with the problems named, then the cycle fails rather
            # than the account posting engagement bait in June's mouth.
            voice_problems = style.caption_problems(brief["caption"], persona_id)
            if voice_problems:
                ev("brief", "progress",
                   f"caption broke the voice contract: {'; '.join(voice_problems)[:120]}"
                   " — regenerating")
                log(f"caption broke the voice contract ({'; '.join(voice_problems)[:90]})"
                    " — regenerating once")
                brief = brain.make_brief(top, fmt, voice_problems=voice_problems,
                                         persona_id=persona_id)
                voice_problems = style.caption_problems(brief["caption"], persona_id)
                if voice_problems:
                    raise RuntimeError(
                        "caption still breaks the voice contract after a retry: "
                        + "; ".join(voice_problems))
            # Two styles from one family are one room shown twice. The distance
            # gate finds that too, but only after both have been rendered and
            # paid for — reading their names costs nothing and happens here.
            clashes = (brain.family_clashes(brief.get("frame_specs") or [], video_style)
                       + brain.pale_clashes(brief.get("frame_specs") or [], video_style))
            if clashes:
                ev("brief", "progress", f"styles too close: {'; '.join(clashes)[:120]}"
                                        " — regenerating")
                log(f"styles too close ({'; '.join(clashes)[:100]}) — regenerating once")
                brief = brain.make_brief(top, fmt, persona_id=persona_id,
                                         style=video_style,
                                         voice_problems=None,
                                         avoid_subjects=None)
                still = (brain.family_clashes(brief.get("frame_specs") or [], video_style)
                         + brain.pale_clashes(brief.get("frame_specs") or [], video_style))
                for note in still:
                    # not fatal: the distance gate is downstream and will drop a
                    # room that really does repeat, which is a four-style reel
                    # rather than a dead cycle
                    log(f"  WARNING: {note}")
                    ev("brief", "progress", f"still close: {note}")
            leaks = brain.real_subject_leaks(top, brief["image_prompts"],
                                            persona_id=persona_id)
            if leaks:
                ev("brief", "progress", f"real subjects in image prompts: {', '.join(leaks)}"
                                        " — regenerating")
                log(f"image prompts named real subjects ({', '.join(leaks)}) — regenerating once")
                brief = brain.make_brief(top, fmt, avoid_subjects=leaks,
                                         persona_id=persona_id)
                leaks = brain.real_subject_leaks(top, brief["image_prompts"],
                                            persona_id=persona_id)
                if leaks:
                    raise RuntimeError(
                        f"image prompts still name real subjects after a retry: "
                        f"{', '.join(leaks)}. Refusing to render a synthetic depiction of "
                        f"something a viewer could look up.")
        brief_id = store.save_brief(con, top_id, {**brief,
                                    "format": "hero_clip" if args.hero else fmt})
        ev("brief", "done", brief["premise"])
        # the brief travels with its own frames, so --from-run can buy the
        # transitions against the exact names and captions they were made for
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "brief.json").write_text(
            json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"brief #{brief_id}: {brief['premise']}")
        log(f"caption: {brief['caption']!r}")

        # ── 4 · generate assets ────────────────────────────────
        provenance = {"model": "", "credit": {}}
        carousel_paths, cover_path, quality_notes, room_frames = [], "", [], []
        drop_notes = []
        if args.hero:
            log("rendering hero clip (Wan text-to-video — takes a few minutes)…")
            ev("render", "running", "Wan text-to-video (minutes-long render)")
            video_path, model, credit = factory.hero_clip(
                brief["image_prompts"][0], run_dir)
            store.save_asset(con, brief_id, "video", video_path, model,
                             brief["image_prompts"][0], chosen=True,
                             meta={"credit": credit} if credit else {})
            ev("render", "done", f"hero clip via {model}")
            log(f"  video source: {model}")
            provenance = {"model": model, "credit": credit}
            media, media_kind, alt = video_path, "video", brief["alt_text"]
        else:
            n_prompts = len(brief["image_prompts"])
            # A spec-labelled slideshow is a comparison: every frame must be
            # the SAME room. One shared seed buys that, and it only holds if
            # each prompt contributes exactly one render — a judge picking
            # candidate 0 here and candidate 1 there would reintroduce the
            # drift the seed just removed. The beauty contest is the right
            # thing to trade away: matching frames are the whole format.
            comparison = bool(brief.get("frame_specs") and any(brief["frame_specs"]))
            vertical = (fmt == "slideshow_video"
                        and look["aspect"].strip().lower() == "vertical")
            canvas = factory.VERTICAL if vertical else factory.SQUARE
            # rendered at the delivery shape, not cropped down to it
            image_size = ({"width": canvas[0], "height": canvas[1]}
                          if vertical else "square_hd")
            shots = 1 if comparison else 2
            seed = int(time.time()) % 2_000_000_000 if comparison else None
            # BOARD styles alternate a full-screen texture board — the
            # materials ARE the picture, names written on the bands — with the
            # room they build, and the room frame stays clean of text. The
            # boards are derived from each scheme's label, so the brain
            # changes not at all.
            board = comparison and look["label_style"] == "board"
            rooms = list(brief["image_prompts"])
            boards = ([factory.board_prompt(factory.parse_spec(x))
                       for x in brief["frame_specs"]] if board else [])
            render_prompts = ([q for pair in zip(boards, rooms) for q in pair]
                              if board else list(rooms))
            if board:
                log(f"  {video_style['name']}: {len(boards)} texture boards interleaved")
            # media_source is the persona's budget decision: "stock" sources
            # licensed photos and never touches paid generation
            prefer = str((who.get("content") or {}).get("media_source")
                         or "generated").strip().lower()
            log(f"generating {n_prompts} prompt(s) × {shots} candidate(s)"
                + (f" · comparison set, shared seed {seed}" if comparison else "")
                + (" (stock-first — no paid generation)" if prefer == "stock" else "")
                + "…")
            ev("render", "running", f"{n_prompts} prompts × {shots} candidates"
                                    + (" · comparison seed" if comparison else "")
                                    + (" · stock-first" if prefer == "stock" else ""))
            changes = brief.get("frame_changes") or []
            specs_all = list(brief.get("frame_specs") or [])
            morph = look["assembly"] == "morph"
            before_img = None
            if morph and args.from_run:
                # The frames are the cheap half and they are already on disk.
                # Re-rendering them to buy transitions costs another $0.15
                # AND changes the pictures the operator just approved, which
                # is the worse of the two problems.
                src = Path(args.from_run)
                if not src.is_absolute():
                    src = ASSETS_DIR / src.name
                keys = sorted(src.glob("room_v*.jpg"),
                              key=lambda p: int(p.stem.split("_v")[1]))
                base = next(iter(src.glob("before_*.jpg")), None)
                if not base or not keys:
                    raise RuntimeError(
                        f"--from-run {src} has no before_*.jpg / room_v*.jpg "
                        f"keyframes to build from")
                before_img = {"path": str(base), "model": "reused", "url": ""}
                cands = [{"path": str(p), "prompt": rooms[i] if i < len(rooms)
                          else str(p), "model": "reused", "url": ""}
                         for i, p in enumerate(keys)]
                rooms = [c["prompt"] for c in cands]
                brief["frame_specs"] = specs_all[:len(cands)]
                brief["image_prompts"] = list(rooms)
                render_prompts = list(rooms)
                log(f"  reusing {len(cands)} keyframes from {src.name} "
                    f"— nothing rendered, ~$0.15 not spent again")
                ev("render", "done", f"reused {len(cands)} keyframes from {src.name}")
            elif morph and changes:
                # A morph style renders the room BEFORE any decision — tired,
                # cluttered, nobody's idea of a nice room — and then edits it
                # into all five styles. That frame is the hook: the reference
                # spends its first two seconds on the version you would not
                # want, which is the only reason the fifth one lands.
                log(f"  morph mode: 1 before-room + {len(changes)} styles, "
                    f"joined by generated transitions")
                before_img = factory.generate_images(
                    [brief.get("base_prompt") or rooms[0]], run_dir, per_prompt=1,
                    prefer=prefer, seed=seed, image_size=image_size,
                    tag="before")[0]
                variants = factory.generate_variants(before_img, changes, run_dir,
                                                     canvas=canvas, mode="reskin",
                                                     labels=specs_all)
                kept, dropped = [], []
                for i, v in enumerate(variants):
                    (dropped if v.get("mismatch") else kept).append(
                        dict(v, prompt=rooms[i], scheme=i))
                for v in dropped:
                    log(f"  DROPPED style {v['scheme'] + 1}: {v['mismatch']}")
                    ev("render", "progress", f"dropped: {v['mismatch']}")
                drop_notes = [f"dropped — {v['mismatch']}" for v in dropped]
                if dropped:
                    keep = [v["scheme"] for v in kept]
                    rooms = [rooms[i] for i in keep]
                    brief["frame_specs"] = [specs_all[i] for i in keep]
                    brief["image_prompts"] = list(rooms)
                render_prompts = list(rooms)
                cands = kept
                if len(kept) < 2:
                    raise RuntimeError(
                        f"only {len(kept)} style survived the change gate — a "
                        f"morph reel needs at least two rooms to move between. "
                        f"Refusing to pay for transitions between duplicates.")
                # the before-room never reaches the judge loop below (it has
                # no prompt of its own among render_prompts), so its
                # provenance is recorded here or nowhere
                store.save_asset(con, brief_id, "image", before_img["path"],
                                 before_img["model"],
                                 brief.get("base_prompt") or rooms[0], chosen=True,
                                 meta={"role": "before"})
            elif look["image_mode"] == "edit" and comparison and len(changes) > 1:
                # ONE room is rendered; every other scheme is an EDIT of it, so
                # the geometry cannot drift between frames. Boards are their
                # own pictures and stay text-to-image.
                log(f"  edit mode: 1 base room + {len(changes) - 1} edited "
                    f"schemes (the room cannot drift)")
                base = factory.generate_images(rooms[:1], run_dir, per_prompt=1,
                                               prefer=prefer, seed=seed,
                                               image_size=image_size, tag="room")[0]
                variants = factory.generate_variants(base, changes[1:], run_dir,
                                                     canvas=canvas,
                                                     labels=specs_all[1:])
                # match the room prompts the assembly is about to look for
                # A scheme that still matches another after its retry is
                # dropped, not shipped with a warning: four true schemes are
                # a good reel, five with one repeat is the thing a viewer
                # notices and calls fake. The images are already paid for
                # either way — the only question is whether they go out.
                kept, dropped = [], []
                for i, v in enumerate(variants):
                    (dropped if v.get("mismatch") else kept).append(
                        dict(v, prompt=rooms[i + 1], scheme=i + 1))
                for v in dropped:
                    log(f"  DROPPED scheme {v['scheme'] + 1}: {v['mismatch']}")
                    ev("render", "progress", f"dropped: {v['mismatch']}")
                # the removal is not silent: the draft says a scheme was cut
                # and why, so a four-frame reel is a decision, not a mystery
                drop_notes = [f"dropped — {v['mismatch']}" for v in dropped]
                cands = [base] + kept
                if dropped:
                    keep_idx = [0] + [v["scheme"] for v in kept]
                    rooms = [rooms[i] for i in keep_idx]
                    brief["frame_specs"] = [specs_all[i] for i in keep_idx]
                    brief["image_prompts"] = list(rooms)
                    boards = [factory.board_prompt(factory.parse_spec(x))
                              for x in brief["frame_specs"]] if board else []
                    render_prompts = ([q for pair in zip(boards, rooms) for q in pair]
                                      if board else list(rooms))
                if boards:
                    # rendered AFTER the drop, so a scheme that did not make
                    # it never costs a board
                    cands += factory.generate_images(boards, run_dir, per_prompt=1,
                                                     prefer=prefer,
                                                     image_size=image_size,
                                                     tag="board")
            else:
                cands = factory.generate_images(render_prompts, run_dir,
                                                per_prompt=shots, prefer=prefer,
                                                seed=seed, image_size=image_size)
            ev("render", "progress", f"{len(cands)} candidates rendered — judging")
            chosen_paths = []
            for pi, prompt in enumerate(render_prompts):
                group = [c for c in cands if c["prompt"] == prompt]
                pick, reason = factory.judge_pick(group, brief["premise"],
                                                  persona_id=persona_id)
                for gi, c in enumerate(group):
                    meta = {"judge_reason": reason} if gi == pick else {}
                    if c.get("credit"):
                        meta["credit"] = c["credit"]
                    store.save_asset(con, brief_id, "image", c["path"], c["model"],
                                     prompt, chosen=(gi == pick), meta=meta)
                chosen_paths.append(group[pick]["path"])
                if pi == 0:
                    provenance = {"model": group[pick]["model"],
                                  "credit": group[pick].get("credit") or {},
                                  # the provider's own public URL, when there is
                                  # one: Instagram fetches media by URL and keeps
                                  # its own copy, so a generated still needs no
                                  # re-hosting at all
                                  "source_url": group[pick].get("url", "")}
                log(f"  prompt {pi}: judge picked candidate {pick} ({reason})")
            renderer = cands[0]["model"] if cands else "?"
            log(f"  image source: {renderer}")
            ev("render", "done", f"{len(cands)} rendered via {renderer}, "
                                 f"{len(chosen_paths)} chosen by judge")

            if fmt == "slideshow_video" and morph and before_img:
                # ── the morph reel ─────────────────────────────
                # No cuts anywhere: every change between rooms is a clip a
                # video model generated from the two frames around it. That
                # is the format, and it is also where the money goes — one
                # paid transition per style, so the count is logged.
                labels = brief.get("frame_specs") or []
                opening = str(brief.get("opening_line") or "").strip()
                secs = args.secs_per_frame or look["secs_per_frame"]
                # ── the money gate ─────────────────────────────
                # The transitions ARE the cost of this style: ~$1.00 against
                # ~$0.15 for everything before them. So nothing is bought
                # until the frames are known good. A five-style reel that
                # has three styles is not this format, and paying $0.60 to
                # assemble it — which is exactly what happened on
                # 2026-08-17 — is buying a product already known to be
                # wrong. The frames are already paid for and kept; the
                # operator re-runs when the brief is right.
                if args.frames_only:
                    log(f"  --frames-only: {len(chosen_paths)} styles rendered "
                        f"and verified, no transitions bought (saved "
                        f"~${0.20 * len(chosen_paths):.2f})")
                    ev("assemble", "done", "frames only — no video bought")
                    log(f"FRAMES ONLY — {run_dir}")
                    store.finish_cycle(con, cycle_id, "dry_run", "frames only")
                    return 0
                log(f"  assembling: {look['before_secs']}s before-room + "
                    f"{len(chosen_paths)} × {secs}s morphs"
                    + (f" · opening line {opening!r}" if opening else ""))
                ev("assemble", "running",
                   f"{len(chosen_paths)} generated transitions")
                need = int((look["frames"] or [5])[0])
                if len(chosen_paths) < need:
                    raise RuntimeError(
                        f"only {len(chosen_paths)} of {need} styles survived "
                        f"the change gate — refusing to buy "
                        f"{len(chosen_paths)} transitions "
                        f"(~${0.20 * len(chosen_paths):.2f}) for a reel that "
                        f"is already short. Frames are in {run_dir}. "
                        f"Re-run when the brief is right; --frames-only "
                        f"iterates for ~$0.15.")
                built = factory.make_morph_video(
                    before_img["path"], chosen_paths, labels, run_dir,
                    before_secs=look["before_secs"], secs_per_style=secs,
                    hold=look["label_hold"], opening_line=opening,
                    voice=look["voiceover"], music=look["music"], canvas=canvas)
                video_path = built["path"]
                room_frames = list(chosen_paths)
                log(f"  {built['seconds']:.1f}s · ${built['spend']:.2f} in "
                    f"transitions · {', '.join(built['models'])}")
                ev("assemble", "progress",
                   f"{built['seconds']:.1f}s, ${built['spend']:.2f} of video")
                # the grid thumbnail must be a room somebody wants, never the
                # tired one the reel deliberately opens on
                cover_path = factory.burn_centre(
                    chosen_paths[0], factory.style_name(labels[0] if labels else ""),
                    run_dir / "cover.jpg", canvas, look["label_height"])
                quality_notes = drop_notes + built["notes"]
                for note in quality_notes:
                    log(f"  QUALITY: {note}")
                    ev("assemble", "progress", f"quality: {note}")
                store.save_asset(con, brief_id, "video", video_path,
                                 "+".join(built["models"]) or "morph", chosen=True,
                                 meta={"spend_usd": built["spend"],
                                       "seconds": built["seconds"]})
                ev("assemble", "done", "morph.mp4")
            elif fmt == "slideshow_video":
                # a persona may declare its slides silent (June: the operator
                # adds trending audio in the app — the API cannot, and a TTS
                # voice would break her voice contract)
                wants_voice = look["voiceover"]
                audio_path = None
                if wants_voice:
                    log("voiceover + ffmpeg slideshow assembly…")
                    ev("voiceover", "running", "TTS voiceover")
                    audio_path, tts_model = factory.tts(brief["voiceover_script"], run_dir)
                    store.save_asset(con, brief_id, "audio", audio_path, tts_model,
                                     brief["voiceover_script"], chosen=True)
                    ev("voiceover", "done", f"via {tts_model}")
                else:
                    log("silent slideshow (operator adds audio in the app)…")
                ev("assemble", "running", "ffmpeg slideshow")
                # a style that puts nothing on the picture (board) still
                # needs the labels here — they are burned onto its boards
                labels = brief.get("frame_specs") or []
                if labels and any(labels):
                    log(f"  burning {sum(1 for x in labels if x)} spec labels into frames")
                if vertical:
                    log(f"  vertical 9:16 delivery ({canvas[0]}×{canvas[1]}) — "
                        f"fills the phone in Reels")
                # NOT `style`: this module imports studio.style, and a local
                # of the same name makes every earlier use of the module a
                # read of an unassigned local
                label_style = look["label_style"]
                cut = look["cut"]
                secs = args.secs_per_frame or look["secs_per_frame"]
                durations = None
                if board:
                    # names go on the texture boards; the room frames carry
                    # no text at all — that is the format
                    for bi in range(0, len(chosen_paths), 2):
                        spec = brief["frame_specs"][bi // 2]
                        pairs = factory.parse_spec(spec)
                        # a board that prints #2D5F2E beside a beige band
                        # breaks the one promise this format makes
                        fixed, notes = factory.correct_bands(
                            chosen_paths[bi], pairs,
                            run_dir / f"board-{bi // 2}-fixed.png", canvas)
                        for note in notes:
                            log(f"  board {bi // 2 + 1}: {note}")
                        chosen_paths[bi] = factory.burn_band_names(
                            fixed, spec, run_dir / f"board-{bi // 2}.png", canvas)
                    board_secs = look["board_secs"]
                    durations = [board_secs, secs] * (len(chosen_paths) // 2)
                    labels, label_style = None, "none"
                    # the payoff first: every finished room flashed in about
                    # a second, before the first board asks for patience
                    if look["hook"] == "flash" and len(room_frames) > 1:
                        chosen_paths = list(room_frames) + chosen_paths
                        durations = [look["hook_secs"]] * len(room_frames) + durations
                        log(f"  hook: {len(room_frames)} rooms flashed in "
                            f"{look['hook_secs'] * len(room_frames):.1f}s before the sequence")
                log(f"  cut: {cut or 'fade'} · {secs}s per frame · labels: {label_style}")
                video_path = factory.make_slideshow(chosen_paths, audio_path, run_dir,
                                                    secs_per_image=secs, labels=labels,
                                                    canvas=canvas,
                                                    label_style=label_style, cut=cut,
                                                    durations=durations)
                # the reel's own room frames, kept for the carousel twin: in
                # a board style the rooms are every second frame
                room_frames = (chosen_paths[1::2] if board else list(chosen_paths))
                # Reels opens on a frame the app picks, which is whatever
                # lands at its default offset — a mid-cut smear as often as
                # not. This is the frame worth opening on, ready to choose.
                cover_path = ""
                if room_frames:
                    cover_path = factory.burn_spec_card(
                        room_frames[0], (brief.get("frame_specs") or [""])[0],
                        run_dir / "cover.jpg", canvas) if label_style == "card" \
                        else str(room_frames[0])
                # What the run could not make true. A scheme whose walls came
                # back the wrong colour contradicts its own board, and the
                # operator should be told rather than left to spot it.
                quality_notes = drop_notes + [c["mismatch"] for c in cands
                                              if c.get("mismatch")]
                for note in quality_notes:
                    log(f"  QUALITY: {note}")
                    ev("assemble", "progress", f"quality: {note}")

                store.save_asset(con, brief_id, "video", video_path, "ffmpeg-slideshow",
                                 chosen=True)
                ev("assemble", "done", "slideshow.mp4")

            if fmt == "slideshow_video":
                # ── the carousel twin ──────────────────────────
                # The frames are already paid for. A reel is pushed to people
                # who do not follow us; a carousel is read and SAVED by the
                # ones who do, and saves are the strongest signal the feed has.
                if look.get("carousel_twin") and comparison and room_frames:
                    slides = list(room_frames)
                    specs = list(brief.get("frame_specs") or [])
                    if morph and before_img:
                        # the carousel tells the reel's story, and the story
                        # starts with the room nobody wanted. No spec card on
                        # it: there is nothing yet to specify.
                        slides = [before_img["path"]] + slides
                        specs = [""] + specs
                    carousel_paths = factory.carousel_frames(slides, specs, run_dir)
                    log(f"  carousel twin: {len(carousel_paths)} slides at 4:5")
                    ev("assemble", "progress",
                       f"carousel twin — {len(carousel_paths)} slides")
                media, media_kind, alt = video_path, "video", brief["alt_text"]
            else:
                media, media_kind, alt = chosen_paths[0], "image", brief["alt_text"]

        # which identity produced this asset — the style bible's version stamp
        provenance["style"] = style.style_version(persona_id)
        # and which NAMED VIDEO STYLE shot it, so performance can be
        # attributed to a format rather than to a vague sense of what works
        if video_style:
            provenance["format"] = video_style["id"]

        # ── 5 · publish (the gate lives in publisher.py) ───────
        if args.dry_run:
            store.save_post(con, brief_id, targets[0] if targets else "none",
                            "", "", brief["caption"], "dry_run")
            store.finish_cycle(con, cycle_id, "dry_run")
            ev("publish", "done", "DRY RUN — nothing published")
            log(f"DRY RUN — media at {media}, nothing published")
            return 0

        # ── 4b · one brief → a native cut per platform ─────────
        log(f"adapting the brief for: {', '.join(targets)}")
        ev("adapt", "running", f"{len(targets)} platform renditions")
        rends = adapt.renditions(brief, targets, persona_id=persona_id)
        store.save_renditions(con, brief_id, rends, brief.get("model", ""))
        for pl, r in rends.items():
            preview = r.get("title") or (r.get("text") or "")[:70]
            log(f"  {pl}: {preview!r}")
        ev("adapt", "done", " · ".join(f"{p}:{len(r.get('text') or r.get('description',''))}c"
                                       for p, r in rends.items()))

        log(f"publishing to: {', '.join(targets)}")
        ev("publish", "running", f"pre-publish gate → {', '.join(targets)}")
        published, queued, failed = [], [], []

        for platform in targets:
            r = rends.get(platform, {})
            text = r.get("text") or brief["caption"]
            # An account in approve mode gets everything BUT the publish call:
            # the finished post waits in the console's queue for the operator.
            if guard.publish_mode(platform, persona_id) == "approve":
                # the ledger is the queue (it travels via git to wherever the
                # console runs); the sqlite row is this machine's audit copy
                gid = draftpool.export_draft(
                    {"brief_id": brief_id, "persona": persona_id,
                     "platform": platform, "media_kind": media_kind,
                     "alt": alt, "text": text, "title": r.get("title", ""),
                     "tags": r.get("tags") or [], "provenance": provenance,
                     "frame_specs": brief.get("frame_specs") or [],
                     "quality_notes": quality_notes},
                    media_src=media, cover_src=cover_path or None)
                store.save_draft(con, brief_id, persona_id, platform, media,
                                 media_kind, alt, text,
                                 title=r.get("title", ""), tags=r.get("tags"),
                                 provenance={**provenance, "ledger_id": gid})
                ev("publish", "progress",
                   f"{platform}: held as draft {gid} — approve it in the console")
                log(f"HELD for approval → {platform} draft {gid} "
                    f"(console → Approvals; ledger: data/drafts/pending/)")
                queued.append(platform)
                if carousel_paths and platform == "instagram":
                    # its own draft: the operator releases the reel and the
                    # carousel on different days, and may want only one
                    cid = draftpool.export_draft(
                        {"brief_id": brief_id, "persona": persona_id,
                         "platform": platform, "media_kind": "carousel",
                         "alt": alt, "text": text, "title": r.get("title", ""),
                         "tags": r.get("tags") or [],
                         "provenance": {**provenance, "twin_of": gid},
                         "frame_specs": brief.get("frame_specs") or []},
                        media_src=carousel_paths[0],
                        extra_media=carousel_paths[1:])
                    log(f"HELD for approval → {platform} CAROUSEL draft {cid} "
                        f"({len(carousel_paths)} slides, twin of {gid})")
                    ev("publish", "progress",
                       f"{platform}: carousel twin held as draft {cid}")
                continue
            try:
                result = deliver.publish(platform, r, brief["caption"], media,
                                         media_kind, alt, provenance, persona_id,
                                         hero=args.hero)
                store.save_post(con, brief_id, platform, result["uri"], result["url"],
                                r.get("title") or text, "published")
                ev("publish", "progress", f"{platform}: {result['url']}")
                log(f"LIVE on {platform} → {result['url']}")
                published.append(platform)
            except Exception as e:
                msg = str(e)[:200]
                store.save_post(con, brief_id, platform, "", "", brief["caption"], "failed")
                ev("publish", "progress", f"{platform} failed: {msg}")
                log(f"{platform} failed: {msg}")
                failed.append(platform)

        if not published and not queued:
            raise RuntimeError(f"all platforms failed: {', '.join(failed)}")
        outcome = []
        if published:
            outcome.append(f"published: {', '.join(published)}")
        if queued:
            outcome.append(f"awaiting approval: {', '.join(queued)}")
        if failed:
            outcome.append(f"failed: {', '.join(failed)}")
        ev("publish", "done", " · ".join(outcome))
        store.finish_cycle(con, cycle_id,
                           "published" if published else "queued",
                           " · ".join(outcome[1:]) if published else " · ".join(outcome))
        return 0

    except Exception as e:
        store.finish_cycle(con, cycle_id, "failed", str(e)[:300])
        store.log_event(con, cycle_id, "error", "failed", str(e)[:300])
        log(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
