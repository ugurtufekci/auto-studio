#!/usr/bin/env python
"""autoStudio — one fully autonomous cycle: collect → score → brief →
generate → publish → lineage. The S0 spine, end to end, no human.

  python run.py                 # full cycle, publishes to Bluesky
  python run.py --dry-run       # everything except the publish call
  python run.py --format slideshow_video
  python run.py --hero          # true text-to-video post (Wan) instead

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

from studio import adapt, brain, collector, factory, guard, publisher, signals, store  # noqa: E402
from studio import publisher_mastodon as masto  # noqa: E402
from studio import publisher_telegram as tg  # noqa: E402
from studio import publisher_youtube as yt  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_format(forced: str | None) -> str:
    if forced and forced != "auto":
        return forced
    # deterministic by clock so the two daily cron runs alternate formats
    return "image_post" if datetime.now().hour < 14 else "slideshow_video"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no publish call")
    ap.add_argument("--format", default="auto",
                    choices=["auto", "image_post", "slideshow_video"])
    ap.add_argument("--hero", action="store_true",
                    help="true text-to-video post via Wan (demo weapon)")
    ap.add_argument("--now", action="store_true",
                    help="skip the anti-pattern jitter delay (interactive/demo runs)")
    args = ap.parse_args()

    con = store.connect()

    # ── 0 · guardrails (before anything costs money or touches accounts) ──
    requested = [p.strip() for p in
                 os.environ.get("PLATFORMS", "bluesky").split(",") if p.strip()]
    targets = requested
    if not args.dry_run:
        targets, blocked = guard.allowed_platforms(con, requested)
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
        # ── 1 · collect (all public endpoints — no account touched) ──
        log("collecting trends (bluesky search · reddit · rss · gtrends)…")
        ev("collect", "running", "opening sources")
        raw = collector.collect_all(
            on_progress=lambda src, n: ev("collect", "progress", f"{src}: {n} items"))
        store.update_cycle_raw(con, cycle_id, len(raw))
        ev("collect", "done", f"{len(raw)} raw items")
        log(f"cycle #{cycle_id}: {len(raw)} raw items")
        if len(raw) < 5:
            raise RuntimeError("too few raw items collected")

        # ── 2 · normalize & score ──────────────────────────────
        log("normalizing into typed signals…")
        ev("signals", "running", f"LLM typing {len(raw)} items")
        sigs = signals.normalize(raw)
        sig_ids = store.save_signals(con, cycle_id, sigs)
        for s in sigs[:5]:
            log(f"  {s['score']:.2f} [{s['signal_type']}] {s['topic']}")
        if not sigs:
            raise RuntimeError("no signals survived the gates")
        ev("signals", "done", f"{len(sigs)} signals · top: “{sigs[0]['topic']}” ({sigs[0]['score']:.2f})")

        # ── 3 · brief ──────────────────────────────────────────
        top, top_id = sigs[0], sig_ids[0]
        store.mark_chosen_signal(con, top_id)
        fmt = "image_post" if args.hero else pick_format(args.format)
        log(f"chosen signal: “{top['topic']}” → format: {'hero_clip' if args.hero else fmt}")
        ev("brief", "running", f"writing brief for “{top['topic']}”")
        brief = brain.make_brief(top, fmt)
        if guard.is_duplicate_caption(con, brief["caption"]):
            ev("brief", "progress", "caption duplicated a recent post — regenerating")
            log("caption too similar to a recent post — regenerating once")
            brief = brain.make_brief(top, fmt, avoid_captions=[brief["caption"]])
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
            log(f"generating {n_prompts} prompt(s) × 2 candidates…")
            ev("render", "running", f"{n_prompts} prompts × 2 candidates")
            cands = factory.generate_images(brief["image_prompts"], run_dir, per_prompt=2)
            ev("render", "progress", f"{len(cands)} candidates rendered — judging")
            chosen_paths = []
            for pi, prompt in enumerate(brief["image_prompts"]):
                group = [c for c in cands if c["prompt"] == prompt]
                pick, reason = factory.judge_pick(group, brief["premise"])
                for gi, c in enumerate(group):
                    meta = {"judge_reason": reason} if gi == pick else {}
                    if c.get("credit"):
                        meta["credit"] = c["credit"]
                    store.save_asset(con, brief_id, "image", c["path"], c["model"],
                                     prompt, chosen=(gi == pick), meta=meta)
                chosen_paths.append(group[pick]["path"])
                if pi == 0:
                    provenance = {"model": group[pick]["model"],
                                  "credit": group[pick].get("credit") or {}}
                log(f"  prompt {pi}: judge picked candidate {pick} ({reason})")
            renderer = cands[0]["model"] if cands else "?"
            log(f"  image source: {renderer}")
            ev("render", "done", f"{len(cands)} rendered via {renderer}, "
                                 f"{len(chosen_paths)} chosen by judge")

            if fmt == "slideshow_video":
                log("voiceover + ffmpeg slideshow assembly…")
                ev("voiceover", "running", "TTS voiceover")
                audio_path, tts_model = factory.tts(brief["voiceover_script"], run_dir)
                store.save_asset(con, brief_id, "audio", audio_path, tts_model,
                                 brief["voiceover_script"], chosen=True)
                ev("voiceover", "done", f"via {tts_model}")
                ev("assemble", "running", "ffmpeg slideshow")
                video_path = factory.make_slideshow(chosen_paths, audio_path, run_dir)
                store.save_asset(con, brief_id, "video", video_path, "ffmpeg-slideshow",
                                 chosen=True)
                ev("assemble", "done", "slideshow.mp4")
                media, media_kind, alt = video_path, "video", brief["alt_text"]
            else:
                media, media_kind, alt = chosen_paths[0], "image", brief["alt_text"]

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
        rends = adapt.renditions(brief, targets)
        store.save_renditions(con, brief_id, rends, brief.get("model", ""))
        for pl, r in rends.items():
            preview = r.get("title") or (r.get("text") or "")[:70]
            log(f"  {pl}: {preview!r}")
        ev("adapt", "done", " · ".join(f"{p}:{len(r.get('text') or r.get('description',''))}c"
                                       for p, r in rends.items()))

        log(f"publishing to: {', '.join(targets)}")
        ev("publish", "running", f"pre-publish gate → {', '.join(targets)}")
        published, failed = [], []

        for platform in targets:
            try:
                r = rends.get(platform, {})
                text = r.get("text") or brief["caption"]
                if platform == "bluesky":
                    client = publisher.login()   # only authenticated touch per cycle
                    result = (publisher.post_video(client, text, media, alt, provenance)
                              if media_kind == "video"
                              else publisher.post_image(client, text, media, alt, provenance))
                elif platform == "telegram":
                    if not tg.configured():
                        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL not set")
                    result = (tg.post_video(text, media, alt, provenance)
                              if media_kind == "video"
                              else tg.post_image(text, media, alt, provenance))
                elif platform == "mastodon":
                    if not masto.configured():
                        raise RuntimeError("MASTODON_INSTANCE / MASTODON_TOKEN not set")
                    result = (masto.post_video(text, media, alt, provenance)
                              if media_kind == "video"
                              else masto.post_image(text, media, alt, provenance))
                elif platform == "youtube":
                    if not yt.configured():
                        raise RuntimeError("YOUTUBE_* credentials not set "
                                           "(see scripts/youtube_auth.py)")
                    if media_kind != "video":
                        raise RuntimeError("youtube needs a video — run with "
                                           "--format slideshow_video")
                    result = yt.post_video(media, r.get("title", brief["premise"]),
                                           r.get("text") or r.get("description", ""),
                                           r.get("tags", []), provenance)
                else:
                    raise RuntimeError(f"no adapter for platform '{platform}'")
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

        if not published:
            raise RuntimeError(f"all platforms failed: {', '.join(failed)}")
        ev("publish", "done", f"published: {', '.join(published)}"
                              + (f" · failed: {', '.join(failed)}" if failed else ""))
        store.finish_cycle(con, cycle_id, "published",
                           f"failed: {', '.join(failed)}" if failed else "")
        return 0

    except Exception as e:
        store.finish_cycle(con, cycle_id, "failed", str(e)[:300])
        store.log_event(con, cycle_id, "error", "failed", str(e)[:300])
        log(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
