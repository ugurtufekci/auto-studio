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
    guard,
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
        if args.live_collect:
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
        log(f"chosen signal: “{top['topic']}” → format: {'hero_clip' if args.hero else fmt}")
        ev("brief", "running", f"writing brief for “{top['topic']}”")
        brief = brain.make_brief(top, fmt, persona_id=persona_id)
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
        leaks = brain.real_subject_leaks(top, brief["image_prompts"])
        if leaks:
            ev("brief", "progress", f"real subjects in image prompts: {', '.join(leaks)}"
                                    " — regenerating")
            log(f"image prompts named real subjects ({', '.join(leaks)}) — regenerating once")
            brief = brain.make_brief(top, fmt, avoid_subjects=leaks,
                                     persona_id=persona_id)
            leaks = brain.real_subject_leaks(top, brief["image_prompts"])
            if leaks:
                raise RuntimeError(
                    f"image prompts still name real subjects after a retry: "
                    f"{', '.join(leaks)}. Refusing to render a synthetic depiction of "
                    f"something a viewer could look up.")
        brief_id = store.save_brief(con, top_id, {**brief,
                                    "format": "hero_clip" if args.hero else fmt})
        ev("brief", "done", brief["premise"])
        log(f"brief #{brief_id}: {brief['premise']}")
        log(f"caption: {brief['caption']!r}")

        # ── 4 · generate assets ────────────────────────────────
        provenance = {"model": "", "credit": {}}
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
            comparison = (bool(brief.get("frame_specs") and any(brief["frame_specs"]))
                          and bool((who.get("content") or {}).get("slideshow_spec_overlay")))
            shots = 1 if comparison else 2
            seed = int(time.time()) % 2_000_000_000 if comparison else None
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
            cands = factory.generate_images(brief["image_prompts"], run_dir,
                                            per_prompt=shots, prefer=prefer, seed=seed)
            ev("render", "progress", f"{len(cands)} candidates rendered — judging")
            chosen_paths = []
            for pi, prompt in enumerate(brief["image_prompts"]):
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

            if fmt == "slideshow_video":
                # a persona may declare its slides silent (June: the operator
                # adds trending audio in the app — the API cannot, and a TTS
                # voice would break her voice contract)
                wants_voice = ((persona.load(persona_id).get("content")
                                or {}).get("slideshow_voiceover", True))
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
                # spec labels are a comparison-slideshow device (June's
                # colour swaps); a persona whose slides are atmosphere keeps
                # its picture clean
                content_cfg = persona.load(persona_id).get("content") or {}
                labels = (brief.get("frame_specs") or []
                          if content_cfg.get("slideshow_spec_overlay") else None)
                if labels and any(labels):
                    log(f"  burning {sum(1 for x in labels if x)} spec labels into frames")
                video_path = factory.make_slideshow(chosen_paths, audio_path, run_dir,
                                                    labels=labels)
                store.save_asset(con, brief_id, "video", video_path, "ffmpeg-slideshow",
                                 chosen=True)
                ev("assemble", "done", "slideshow.mp4")
                media, media_kind, alt = video_path, "video", brief["alt_text"]
            else:
                media, media_kind, alt = chosen_paths[0], "image", brief["alt_text"]

        # which identity produced this asset — the style bible's version stamp
        provenance["style"] = style.style_version(persona_id)

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
                     "tags": r.get("tags") or [], "provenance": provenance},
                    media_src=media)
                store.save_draft(con, brief_id, persona_id, platform, media,
                                 media_kind, alt, text,
                                 title=r.get("title", ""), tags=r.get("tags"),
                                 provenance={**provenance, "ledger_id": gid})
                ev("publish", "progress",
                   f"{platform}: held as draft {gid} — approve it in the console")
                log(f"HELD for approval → {platform} draft {gid} "
                    f"(console → Approvals; ledger: data/drafts/pending/)")
                queued.append(platform)
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
