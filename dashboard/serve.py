#!/usr/bin/env python
"""autoStudio ops console — handbook page 18 as a real app shell.

Separate screens, one page:
  Overview  — "is everything okay?": fleet summary, exceptions, latest cycle
  Pipeline  — live stage view + cycle history → per-cycle drill-down
  Personas  — the fleet wall: filters, search, exception queue
  Content   — published posts with full lineage
  Assets    — generated media gallery

Run:  python dashboard/serve.py   →   http://localhost:8377
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import httpx  # noqa: E402

from studio import metrics, persona, pool, remediation, store  # noqa: E402

ASSETS_DIR = ROOT / "assets"
PORT = 8377

_profile_cache = {"t": 0.0, "data": None, "takedown": False}


def bsky_profile() -> dict:
    """Public profile info + suspension flag, no auth. Cached 60s."""
    if time.time() - _profile_cache["t"] < 60:
        return _profile_cache
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                      params={"actor": os.environ.get("BLUESKY_HANDLE", "")}, timeout=8)
        _profile_cache["data"] = r.json() if r.status_code == 200 else None
        _profile_cache["takedown"] = r.status_code != 200 and "AccountTakedown" in r.text
    except Exception:
        _profile_cache["data"] = None
    _profile_cache["t"] = time.time()
    return _profile_cache


def provider_status(con) -> list[dict]:
    row = con.execute(
        "SELECT detail FROM events WHERE status='failed' AND stage='error' "
        "AND cycle_id=(SELECT MAX(id) FROM cycles) ORDER BY id DESC LIMIT 1"
    ).fetchone()
    err = row["detail"][:120] if row else ""

    def flag(key: str, hint: str) -> dict:
        ok = bool(os.environ.get(key))
        note = err if (err and hint in err.lower()) else ""
        return {"ok": ok, "note": note}

    if os.environ.get("LLM_PROVIDER", "claude_code") == "claude_code":
        brain = {"name": "Claude (Max plan)", "role": "signals · briefs · judge",
                 "ok": True, "note": "local CLI — no API credits", "info": True}
    else:
        brain = {"name": "Anthropic API", "role": "signals · briefs · judge",
                 **flag("ANTHROPIC_API_KEY", "credit")}
    tg_ok = all(os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL"))
    masto_ok = all(os.environ.get(k) for k in ("MASTODON_INSTANCE", "MASTODON_TOKEN"))
    yt_ok = all(os.environ.get(k) for k in
                ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    return [
        # one card per platform adapter run.py has — unconfigured ones stay visible
        # on purpose, so "what could this studio publish to" needs no code dive
        {"name": "Bluesky", "role": "publishing",
         "ok": bool(os.environ.get("BLUESKY_APP_PASSWORD")), "note": "",
         "missing": "app password missing"},
        {"name": "Telegram", "role": "publishing",
         "ok": tg_ok, "info": True, "missing": "bot token / channel missing",
         "note": os.environ.get("TELEGRAM_CHANNEL", "") if tg_ok else ""},
        {"name": "Mastodon", "role": "publishing",
         "ok": masto_ok, "note": "", "missing": "not configured — optional"},
        {"name": "YouTube", "role": "publishing · video",
         "ok": yt_ok, "note": "", "missing": "not configured — optional"},
        {"name": "fal.ai", "role": "images · video · voice", **flag("FAL_KEY", "balance")},
        brain,
        # revenue-platform roadmap — the money goal stays visible on the board.
        # Only platforms with a real monetization path belong here (brand deals,
        # affiliate, creator funds); X sits last because its write API is paid.
        {"name": "Instagram", "role": "roadmap · revenue", "ok": False, "planned": True,
         "missing": "adapter not built — Reels via Graph API"},
        {"name": "TikTok", "role": "roadmap · revenue", "ok": False, "planned": True,
         "missing": "adapter not built — Content Posting API"},
        {"name": "X", "role": "roadmap · revenue", "ok": False, "planned": True,
         "missing": "after IG/TikTok — write API is paid"},
    ]


def state() -> dict:
    con = store.connect()
    # The sidebar shows the studio's default persona; the fleet wall shows all
    # of them. Which one is "default" is the same question run.py asks.
    who = {}
    try:
        p = persona.load()
        who = {"id": p.get("id", ""), "name": p["identity"]["name"],
               "tagline": p["identity"]["tagline"],
               "handle": p["identity"].get("handle", ""),
               "cadence": f"{p['content']['posts_per_day']}/day"}
    except Exception:
        pass
    prof = bsky_profile()
    pd = prof["data"] or {}
    # the sidebar card shows every platform leg, not just Bluesky
    try:
        real = [r for r in store.fleet(con) if not r["demo"]]
        match = [r for r in real if r["name"].lower() == who.get("name", "").lower()]
        who["accounts"] = (match or real or [{"accounts": []}])[0]["accounts"]
    except Exception:
        who["accounts"] = []
    persona_data = who
    return {
        "persona": persona_data,
        "profile": {"followers": pd.get("followersCount"), "posts": pd.get("postsCount"),
                    "avatar": pd.get("avatar"), "suspended": prof["takedown"]},
        "providers": provider_status(con),
        "cycles": store.cycles_with_events(con, limit=10),
        "stats": store.stats(con),
        "lineage": store.lineage(con)[:15],
        "assets": store.recent_assets(con, limit=30),
        "now": time.strftime("%H:%M:%S"),
    }


def fleet_state() -> dict:
    """Every configured persona with its registry legs.

    The registry (config/accounts.yaml) is the authority on which accounts
    exist, and each persona's config is the authority on who it is; the
    database is just where the console caches that plus live post counts."""
    con = store.connect()
    rows = metrics.fleet_accounts()
    for persona_id in persona.available():
        try:
            p = persona.load(persona_id)
        except Exception:
            continue
        legs = [a for a in rows if a.get("persona") == persona_id]
        cadence = f"{(p.get('content') or {}).get('posts_per_day', 1)}/day"
        primary = legs[0] if legs else {}
        pid = store.ensure_persona(
            con, p["identity"]["name"], primary.get("handle", ""),
            primary.get("platform", ""), persona.category_of(persona_id), cadence)
        # a persona is as healthy as its worst leg
        worst = "suspended" if any(a.get("status") == "suspended" for a in legs) else "active"
        con.execute("UPDATE personas SET status=? WHERE id=?", (worst, pid))
        con.commit()
        for acct in legs:
            store.ensure_account(con, pid, acct["platform"], acct["handle"],
                                 cadence, acct.get("status", "active"))
    return {"personas": store.fleet(con)}


def pool_state() -> dict:
    """The shared signal pool as the harvest left it — read straight from
    data/signals/, no DB involved. Raw items are deliberately absent: the
    harvest keeps only their counts, the pool IS the distilled layer."""
    categories = []
    for category in pool.available_pools():
        try:
            sigs, meta = pool.read_signals([category])
            data = pool.load_pool(category)
        except Exception:
            continue
        categories.append({**meta[0], "sources": data.get("sources") or {},
                           "signals": sigs})
    index = {}
    try:
        index = json.loads((pool.POOL_DIR / "index.json").read_text())
    except Exception:
        pass
    return {"categories": categories,
            "source_issues": index.get("source_issues") or [],
            "snapshot": index.get("snapshot", ""),
            "stale_after_hours": pool.STALE_AFTER_HOURS,
            "now": time.strftime("%H:%M:%S")}


def performance_state() -> dict:
    """Live engagement per fleet account plus its git-backed trend ledger
    (written by the shared metrics harvest), joined with what the lineage
    knows about each post — 'what worked' and 'why it exists' in one view."""
    con = store.connect()
    data = metrics.collect()
    topics = {r["url"]: r["topic"] for r in con.execute(
        "SELECT p.url, s.topic FROM posts p JOIN briefs b ON p.brief_id=b.id "
        "JOIN signals s ON b.signal_id=s.id WHERE p.url != ''")}
    accounts = []
    for a in data["accounts"]:
        for p in a.get("posts", []):
            p["topic"] = topics.get(p["url"], "")
        accounts.append({**a, "history": metrics.read_history(a["platform"],
                                                             a["handle"])})
    return {"accounts": accounts, "captured_at": data["captured_at"],
            "now": time.strftime("%H:%M:%S")}


def persona_state(pid: int) -> dict | None:
    con = store.connect()
    p = store.persona_detail(con, pid)
    if not p:
        return None
    for a in p["accounts"]:
        a["diagnosis"] = remediation.diagnose_account(con, p, a)
    return {"persona": p, "catalog": remediation.CATALOG}


APP = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>autoStudio</title><style>
:root{--bg:#0F0F12;--panel:#17171C;--panel2:#1D1D23;--ink:#ECEBE6;--muted:#9C9A92;
--faint:#6E6D67;--hair:#2C2C33;--teal:#1D9E75;--tealt:#9FE1CB;--purple:#7F77DD;
--purplet:#CECBF6;--coral:#D85A30;--coralt:#F5C4B3;--amber:#BA7517;--ambert:#FAC775;
--red:#E24B4A;--redt:#F7C1C1;--gray:#5F5E5A}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,Inter,sans-serif}
a{color:var(--tealt)}
.shell{display:grid;grid-template-columns:212px 1fr;min-height:100vh}
aside{background:var(--panel);border-right:1px solid var(--hair);padding:20px 14px;
position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:4px}
.brand{font-size:16px;font-weight:700;letter-spacing:-.01em;padding:0 8px;margin-bottom:14px}
.brand span{display:block;font-size:9.5px;font-weight:400;letter-spacing:.14em;
text-transform:uppercase;color:var(--faint);margin-top:3px}
nav a{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:8px;
color:var(--muted);text-decoration:none;font-size:13.5px;border-left:2px solid transparent}
nav a:hover{background:var(--panel2);color:var(--ink)}
nav a.on{background:var(--panel2);color:var(--ink);border-left-color:var(--purple)}
nav a .ic{width:16px;text-align:center;opacity:.8}
nav a .pill{margin-left:auto;font-size:10px;background:var(--panel2);border:1px solid var(--hair);
border-radius:9px;padding:0 7px;color:var(--faint)}
nav a .pill.red{color:var(--redt);border-color:var(--red)}
.side-bottom{margin-top:auto;display:flex;flex-direction:column;gap:10px}
.mini{background:var(--panel2);border:1px solid var(--hair);border-radius:10px;padding:10px 12px}
.mini .nm{font-size:13px;font-weight:600;display:flex;gap:7px;align-items:center}
.mini .h{font-size:10.5px;color:var(--faint);margin-top:1px;word-break:break-all}
.mini img{width:26px;height:26px;border-radius:50%;object-fit:cover}
.mini .row{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted);margin-top:5px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:none}
.dot.ok,.dot.active{background:var(--teal)} .dot.warn,.dot.warming{background:var(--amber)}
.dot.bad,.dot.suspended{background:var(--red)} .dot.paused{background:var(--gray)}
.dot.plan{background:transparent;border:1px solid var(--gray)}
main{padding:26px 30px 80px;max-width:1150px;width:100%}
.crumb{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
h1{font-size:21px;letter-spacing:-.01em;margin-bottom:16px;display:flex;align-items:center;gap:12px}
h1 .clock{font-size:11px;color:var(--faint);font-weight:400}
h1 .demob{font-size:10px;letter-spacing:.06em;color:var(--ambert);border:1px solid var(--amber);
border-radius:10px;padding:1px 9px;font-weight:400}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--faint);
margin:24px 0 10px;font-weight:500;display:flex;justify-content:space-between;align-items:baseline}
h2 a{font-size:11px;letter-spacing:0;text-transform:none}
.statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:10px}
.stat{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:11px 12px;text-align:center}
.stat .v{font-size:22px;font-weight:700} .stat .t{font-size:10.5px;color:var(--faint);margin-top:1px}
.stat.attn .v{color:var(--redt)} .stat.good .v{color:var(--tealt)}
.card{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:14px 16px}
.cards3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.card .t{font-size:12px;color:var(--faint)} .card .n{font-size:12px;color:var(--muted);margin-top:2px}
.badge{font-size:10px;padding:1px 8px;border-radius:10px;border:1px solid var(--hair);color:var(--muted)}
.badge.published{color:var(--tealt);border-color:var(--teal)}
.badge.failed{color:var(--redt);border-color:var(--red)}
.badge.dry_run,.badge.running{color:var(--ambert);border-color:var(--amber)}
.pipe{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:16px}
.pipe .head{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:var(--muted);
margin-bottom:12px;flex-wrap:wrap}
.stages{display:flex;flex-wrap:wrap}
.stage{flex:1;min-width:104px;text-align:center;position:relative;padding:0 6px}
.stage::before{content:"";position:absolute;top:11px;left:-50%;width:100%;height:2px;background:var(--hair)}
.stage:first-child::before{display:none}
.stage .b{width:22px;height:22px;border-radius:50%;background:var(--panel2);
border:2px solid var(--hair);margin:0 auto 6px;position:relative}
.stage.done .b{background:var(--teal);border-color:var(--teal)}
.stage.running .b{border-color:var(--tealt);animation:pulse 1s infinite}
.stage.failed .b{background:var(--red);border-color:var(--red)}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(29,158,117,.5)}50%{box-shadow:0 0 0 7px rgba(29,158,117,0)}}
.stage .l{font-size:11px;color:var(--muted)} .stage.done .l,.stage.running .l{color:var(--ink)}
.stage .d{font-size:10px;color:var(--faint);margin-top:2px;min-height:14px}
.evlog{font:11px/1.7 ui-monospace,monospace;color:var(--muted);background:var(--panel2);
border-radius:8px;padding:8px 12px;margin-top:12px;max-height:160px;overflow-y:auto}
.evlog .f{color:var(--redt)}
.exc{background:var(--panel);border:1px solid var(--red);border-radius:12px;
padding:10px 14px;margin-bottom:8px;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.exc b{font-weight:600} .exc .why{color:var(--redt);font-size:12.5px}
.exc .p{color:var(--faint);font-size:11.5px}
.rowitem{background:var(--panel);border:1px solid var(--hair);border-radius:12px;
padding:11px 14px;margin-bottom:8px;display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;
color:var(--muted);font-size:12.5px}
.rowitem b{color:var(--ink)} .rowitem a{margin-left:auto}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.fbtn{font-size:12px;background:var(--panel);border:1px solid var(--hair);border-radius:14px;
padding:3px 12px;color:var(--muted);cursor:pointer}
.fbtn.on{color:var(--tealt);border-color:var(--teal)}
#q{background:var(--panel);border:1px solid var(--hair);border-radius:8px;color:var(--ink);
padding:4px 10px;font-size:12.5px;width:190px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:8px}
.tile{background:var(--panel);border:1px solid var(--hair);border-radius:10px;padding:9px 11px}
.tile.suspended{border-color:var(--red)} .tile.warming{border-color:var(--amber)}
.tile .n{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}
.tile .h{font-size:10.5px;color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile .m{font-size:11px;color:var(--muted);margin-top:3px}
.tile .d{font-size:9px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em;float:right}
/* per-platform legs inside a persona tile */
.legs{margin-top:7px;border-top:1px solid var(--hair);padding-top:5px}
.plic{flex:none;vertical-align:-2px}
.leg .pl{display:flex;align-items:center;gap:6px}
.leg{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--muted);
padding:2px 0}
.leg .pl{color:var(--ink);min-width:58px}
.leg .st{margin-left:auto;font-size:9.5px}
.leg.suspended .pl{color:var(--redt)} .leg.warming .pl{color:var(--ambert)}
.leg.paused .pl{color:var(--faint)}
.acctrow{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;
border-top:1px solid var(--hair);padding:8px 0;font-size:12.5px;color:var(--muted)}
.acctrow:first-of-type{border-top:0} .acctrow b{color:var(--ink)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chips span{font-size:11.5px;background:var(--panel);border:1px solid var(--hair);
border-radius:14px;padding:2px 11px;color:var(--muted)}
.post{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.chain{display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:10px;text-transform:uppercase;
letter-spacing:.07em;color:var(--faint);margin-bottom:10px}
.chain b{border:1px solid;border-radius:20px;padding:1px 9px;font-weight:500}
.chain .sig{color:var(--tealt);border-color:var(--teal)} .chain .bri{color:var(--purplet);border-color:var(--purple)}
.chain .ast{color:var(--coralt);border-color:var(--coral)} .chain .pub{color:var(--ambert);border-color:var(--amber)}
.cap{font-size:14.5px;margin-bottom:8px;white-space:pre-wrap}
.meta{color:var(--muted);font-size:12px;margin:2px 0} .meta b{color:var(--ink);font-weight:500}
.imgs{display:flex;gap:8px;margin:10px 0;flex-wrap:wrap}
.imgs figure{margin:0;text-align:center;max-width:180px}
.imgs img,.imgs video{height:120px;border-radius:8px;border:2px solid transparent;display:block}
.imgs .chosen img,.imgs .chosen video{border-color:var(--teal)}
.imgs figcaption{font-size:9.5px;color:var(--faint);margin-top:2px}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.gal figure{margin:0} .gal img,.gal video{width:100%;aspect-ratio:1;object-fit:cover;border-radius:10px;display:block}
.gal figcaption{font-size:10px;color:var(--faint);margin-top:3px}
.sig{border-left:3px solid var(--hair);margin-bottom:10px} .sig.chosen{border-left-color:var(--teal)}
.sig .tt{font-size:14.5px;font-weight:600} .sig .tt small{color:var(--faint);font-weight:400}
pre{font:11.5px/1.6 ui-monospace,monospace;background:var(--panel2);border-radius:8px;
padding:10px 12px;white-space:pre-wrap;color:var(--muted);margin:6px 0}
.durs{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.durs span{font-size:11px;background:var(--panel2);border-radius:12px;padding:2px 10px;color:var(--muted)}
.empty{color:var(--faint);padding:34px;text-align:center}
.tile{cursor:pointer} .tile:hover{border-color:var(--gray)}
.exc{cursor:pointer} .exc:hover{background:var(--panel2)}
.diag{border-left:3px solid var(--hair)}
.diag.critical,.diag.high{border-left-color:var(--red)}
.diag.medium{border-left-color:var(--amber)} .diag.ok{border-left-color:var(--teal)}
.diag .sy{font-size:15px;font-weight:600;margin-bottom:4px}
.acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.abtn{font-size:12.5px;background:var(--panel2);border:1px solid var(--hair);
border-radius:8px;padding:6px 13px;color:var(--ink);cursor:pointer}
.abtn:hover{border-color:var(--teal);color:var(--tealt)}
.abtn.warn:hover{border-color:var(--amber);color:var(--ambert)}
.abtn:disabled{opacity:.45;cursor:not-allowed}
.abtn.busy{opacity:.6;cursor:wait}
.steps{margin:8px 0 0 0;padding-left:18px;color:var(--muted);font-size:12.5px}
.steps li{margin:3px 0}
.toast{position:fixed;right:18px;bottom:18px;max-width:420px;background:var(--panel);
border:1px solid var(--teal);border-radius:10px;padding:11px 14px;font-size:12.5px;
color:var(--ink);box-shadow:0 8px 24px rgba(0,0,0,.5);z-index:9}
.toast.bad{border-color:var(--red)}
.noteform{display:flex;gap:8px;margin-top:10px}
.noteform input{flex:1;background:var(--panel2);border:1px solid var(--hair);
border-radius:8px;color:var(--ink);padding:6px 10px;font-size:12.5px}
.log{font:11px/1.8 ui-monospace,monospace;color:var(--muted);background:var(--panel2);
border-radius:8px;padding:9px 12px}
.log b{color:var(--ink);font-weight:500}
@media(max-width:880px){.shell{grid-template-columns:1fr}
aside{position:static;height:auto;flex-direction:row;align-items:center;overflow-x:auto;padding:10px 12px}
.brand span,.side-bottom{display:none} nav{display:flex;gap:2px} nav a{padding:6px 9px}
nav a .pill{display:none} main{padding:18px 14px 60px}}
</style></head><body>
<div class="shell">
<aside>
  <div class="brand">autoStudio<span>persona ops console</span></div>
  <nav id="nav"></nav>
  <div class="side-bottom" id="sideinfo"></div>
</aside>
<main id="main"><div class="empty">loading…</div></main>
</div>
<script>
"use strict";
const esc=s=>(s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const T=s=>s?s.slice(11,19):"";
const at=h=>{h=(h||"").toString();return h.startsWith("@")?h:"@"+h};

// Inline platform marks — no external requests, brand-coloured so the platform
// is recognisable at a glance while status stays encoded in the dot/border.
const PLAT={
 bluesky:{c:"#3B9AF8",vb:"0 0 24 20",d:'<path d="M12 10.6C10.9 8.5 8 4.7 5.3 2.8 2.7 1 1.7 1.3 1.1 1.6.4 1.9.3 2.9.3 3.5c0 .6.3 4.7.6 5.4.5 2 2.7 2.6 4.7 2.4-2.9.4-5.5 1.5-2.1 5.3 3.8 4 5.1-.9 6.2-3.5 1.1 2.6 1.9 7.4 6 3.5 3.2-3.5.8-4.9-2.1-5.3 2 .2 4.2-.4 4.7-2.4.3-.7.6-4.8.6-5.4 0-.6-.1-1.6-.8-1.9-.6-.3-1.6-.6-4.2 1.2C16 4.7 13.1 8.5 12 10.6z"/>'},
 telegram:{c:"#2AABEE",vb:"0 0 24 24",d:'<path d="M21.9 3.3 2.4 10.9c-.9.4-.9 1 .2 1.3l4.9 1.5 1.9 5.7c.2.6.4.8 1 .8.5 0 .7-.2 1-.5l2.4-2.3 4.8 3.6c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.2-.5-1.7-1.5-1.3zM8.9 13.6l9.4-5.9c.5-.3.9-.1.5.2l-7.6 6.9-.3 3.5-2-4.7z"/>'},
 instagram:{c:"#E1467C",vb:"0 0 24 24",d:'<rect x="2.6" y="2.6" width="18.8" height="18.8" rx="5.4" fill="none" stroke="currentColor" stroke-width="2.1"/><circle cx="12" cy="12" r="4.4" fill="none" stroke="currentColor" stroke-width="2.1"/><circle cx="17.6" cy="6.5" r="1.35"/>'},
 x:{c:"#E8E8E8",vb:"0 0 24 24",d:'<path d="M17.5 3h3.2l-7 8 7.4 10h-5.8l-4.5-6.2L5.6 21H2.4l7.4-8.4L2.7 3h5.9l4.2 5.8L17.5 3z"/>'},
 youtube:{c:"#FF3B30",vb:"0 0 24 24",d:'<path d="M22.5 7.2c-.3-1-1-1.8-2-2C18.7 4.8 12 4.8 12 4.8s-6.7 0-8.5.4c-1 .3-1.8 1-2 2C1.1 9 1.1 12 1.1 12s0 3 .4 4.8c.3 1 1 1.8 2 2 1.8.5 8.5.5 8.5.5s6.7 0 8.5-.4c1-.3 1.8-1 2-2 .4-1.9.4-4.9.4-4.9s0-3-.4-4.8zM9.8 15.5v-7l5.8 3.5-5.8 3.5z"/>'},
 tiktok:{c:"#FF4D6D",vb:"0 0 24 24",d:'<path d="M16.6 2h-3.1v13.1a2.7 2.7 0 1 1-2.7-2.7c.3 0 .6 0 .8.1V9.4a5.9 5.9 0 1 0 5 5.8V8.4a6.5 6.5 0 0 0 3.9 1.3V6.6a3.6 3.6 0 0 1-3.9-3.5V2z"/>'},
 mastodon:{c:"#6364FF",vb:"0 0 24 24",d:'<path d="M12 2C7 2 4 4 4 9v6c0 4 3 5 6 5l4-.4v-2l-3.6.3c-2.4 0-3.4-.8-3.6-2.6 1.4.4 3 .6 4.6.6 3.6 0 6.6-1.4 6.6-5.9C18.6 4 15.8 2 12 2zm3.6 10.4h-2V8.9c0-.9-.4-1.4-1.2-1.4s-1.2.5-1.2 1.4v3.5H9.4V8.7c0-2 1.2-3.1 2.9-3.1 1 0 1.8.4 2.3 1.2.5-.8 1.3-1.2 2.3-1.2 1.7 0 2.9 1.1 2.9 3.1v3.7h-2V8.9c0-.9-.4-1.4-1.2-1.4s-1.2.5-1.2 1.4v3.5z"/>'},
};
function platIcon(name,size){
  const p=PLAT[(name||"").toLowerCase()], s=size||14;
  if(!p)return `<span class="dot" style="background:var(--gray)"></span>`;
  return `<svg class="plic" viewBox="${p.vb}" width="${s}" height="${s}" `+
    `fill="${p.c}" color="${p.c}" aria-hidden="true">${p.d}</svg>`;
}
const ago=t=>{if(!t)return"never";const h=(Date.now()-new Date(t))/36e5;
  return h<1?Math.round(h*60)+"m ago":h<48?Math.round(h)+"h ago":Math.round(h/24)+"d ago"};
let S=null,F=null,CD={};   // state, fleet, cycle-detail cache
let PQ="",PF="all";        // personas search + filter
let PD=null,PDid=null;     // persona-detail payload + which id
let PL=null,PLopen=null;   // signal-pool payload + which category is expanded
let PM=null;               // performance payload
function toast(msg,bad){
  document.querySelectorAll(".toast").forEach(t=>t.remove());
  const d=document.createElement("div");
  d.className="toast"+(bad?" bad":"");d.textContent=msg;
  document.body.appendChild(d);setTimeout(()=>d.remove(),7000);
}
async function act(pid,action,payload,btn){
  if(btn){btn.classList.add("busy");btn.disabled=true}
  try{
    const r=await fetch("/api/action",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({persona_id:pid,action,payload:payload||""})});
    const j=await r.json();
    toast(j.message||(j.ok?"done":"failed"),!j.ok);
    PD=null;await loadPersona(pid);
  }catch(e){toast("request failed: "+e,true)}
  if(btn){btn.classList.remove("busy");btn.disabled=false}
}
async function loadPersona(pid){
  try{const r=await fetch("/api/persona?id="+pid);
    if(r.ok){PD=await r.json();PDid=String(pid);show()}}catch(e){}
}

// legacy URL redirects
if(location.pathname==="/fleet")history.replaceState(null,"","/#/personas");
if(location.pathname==="/cycle"){const id=new URLSearchParams(location.search).get("id");
  history.replaceState(null,"","/#/cycle/"+(id||""))}

const NAV=[["overview","⌂","Overview"],["pipeline","▶","Pipeline"],
["signals","≋","Signals"],["performance","↗","Performance"],
["personas","▦","Personas"],["content","✎","Content"],["assets","▣","Assets"]];
window.PLtoggle=c=>{PLopen=PLopen===c?null:c;show()};
// Attention is an ACCOUNT-level fact (Mara can be fine on Telegram and
// suspended on Bluesky), so evaluate legs and roll up to the persona.
const acctNeedsAttention=a=>a.status==="suspended"||a.last_cycle_status==="failed"
  ||(a.status==="active"&&a.last_post_at&&(Date.now()-new Date(a.last_post_at))/36e5>36);
const legsOf=p=>p.accounts&&p.accounts.length?p.accounts
  :[{platform:p.platform,handle:p.handle,status:p.status,posts_today:p.posts_today,
     last_post_at:p.last_post_at,last_cycle_status:p.last_cycle_status,demo:p.demo}];
const needsAttention=p=>legsOf(p).some(acctNeedsAttention);
const badLegs=p=>legsOf(p).filter(acctNeedsAttention);
const worstStatus=p=>{const r={suspended:0,warming:1,paused:2,active:3};
  return legsOf(p).map(a=>a.status).sort((x,y)=>(r[x]??9)-(r[y]??9))[0]||"active"};
const acctWhy=a=>a.status==="suspended"?"account suspended — appeal required"
  :a.last_cycle_status==="failed"?"last cycle failed"
  :a.status==="paused"?"paused by operator":"silent >36h despite active status";

function cur(){const h=location.hash.replace(/^#\\/?/,"");const [s,arg]=h.split("/");
  return{s:NAV.some(n=>n[0]===s)||s==="cycle"||s==="persona"?s:"overview",arg}}

function navHTML(){
  const {s}=cur();
  const attn=F?F.personas.filter(needsAttention).length:0;
  const total=F?F.personas.length:null;
  return NAV.map(([k,ic,label])=>{
    let pill="";
    if(k==="personas"&&total!==null)pill=`<span class="pill">${total}</span>`;
    if(k==="overview"&&attn)pill=`<span class="pill red">${attn}</span>`;
    return `<a href="#/${k}" class="${s===k?"on":""}"><span class="ic">${ic}</span>${label}${pill}</a>`
  }).join("");
}

function sideHTML(){
  if(!S)return"";
  const p=S.persona||{},pr=S.profile||{};
  const st=pr.suspended?"suspended":"active";
  const prov=(S.providers||[]).filter(x=>!x.planned).map(x=>{
    const cls=x.ok?((x.note&&!x.info)?"warn":"ok"):"bad";
    return `<div class="row"><span class="dot ${cls}"></span>${esc(x.name)}</div>`}).join("");
  const legs=(p.accounts||[]).map(a=>`<div class="row">
    <span class="dot ${a.status}"></span>
    ${platIcon(a.platform,13)}<span style="color:var(--ink)">${esc(a.platform)}</span>
    <span style="margin-left:auto">${a.posts_today??0} today</span></div>`).join("");
  const n=(p.accounts||[]).length;
  return `<div class="mini">
    <div class="nm">${pr.avatar?`<img src="${esc(pr.avatar)}">`:""}${esc(p.name||"—")}</div>
    <div class="h">${n?`${n} account${n>1?"s":""}`:esc(at(p.handle||""))}</div>
    ${legs}
  </div>
  <div class="mini">${prov}</div>`;
}

function stepperHTML(c,compact){
  const STAGES=[["collect","collect"],["signals","signals"],["brief","brief"],
    ["render","render"],["voiceover","voice"],["assemble","assemble"],["publish","publish"]];
  const by={};for(const e of c.events)(by[e.stage]??=[]).push(e);
  let st="";
  for(const [key,label] of STAGES){
    const evs=by[key]||[];const last=evs[evs.length-1];
    let cls="";if(last)cls=last.status==="done"?"done":last.status==="failed"?"failed":"running";
    const d=(!compact&&last)?esc((last.detail||"").slice(0,90)):"";
    st+=`<div class="stage ${cls}"><div class="b"></div><div class="l">${label}</div><div class="d">${d}</div></div>`}
  return `<div class="stages">${st}</div>`;
}

function pipeBoxHTML(c,compact){
  const err=(c.events||[]).find(e=>e.stage==="error");
  let logh="";
  if(!compact)logh=`<div class="evlog">${c.events.map(e=>
    `<div class="${e.status==='failed'?'f':''}">[${T(e.created_at)}] ${e.stage} · ${e.status} — ${esc(e.detail)}</div>`).join("")}</div>`;
  return `<div class="pipe"><div class="head">
    <span>cycle #${c.id} · ${T(c.started_at)} UTC · ${c.raw_item_count??0} raw items</span>
    <span><a href="#/cycle/${c.id}">full detail →</a> <span class="badge ${c.status}">${c.status}</span></span></div>
    ${stepperHTML(c,compact)}
    ${err?`<div class="evlog"><div class="f">✗ ${esc(err.detail)}</div></div>`:""}${logh}</div>`;
}

// One card PER FAILING ACCOUNT — the queue is a list of broken legs, not personas
function excHTML(p){
  return badLegs(p).map(a=>`<div class="exc" onclick="location.hash='#/persona/${p.id}'">
    <b>${esc(p.name)}</b>
    <span class="badge ${a.status}" style="display:inline-flex;align-items:center;gap:5px">${platIcon(a.platform,12)}${esc(a.platform)}</span>
    <span class="p">${esc(at(a.handle))}</span>
    <span class="why">${acctWhy(a)}</span>
    <span class="p">${a.posts_today??0} today · last post ${ago(a.last_post_at)}</span>
    <span class="p" style="margin-left:auto">manage →</span></div>`).join("");
}

function postHTML(p){
  let imgs="";const th=(p.assets||[]).filter(x=>x.kind==="image");
  if(th.length)imgs='<div class="imgs">'+th.map(x=>
    `<figure class="${x.chosen?"chosen":""}"><img src="/asset?p=${encodeURIComponent(x.path)}">
     <figcaption>${x.chosen?"✓ judge pick":"discarded"}</figcaption></figure>`).join("")+"</div>";
  const vid=(p.assets||[]).find(x=>x.kind==="video");
  if(vid)imgs+=`<div class="imgs"><figure class="chosen"><video src="/asset?p=${encodeURIComponent(vid.path)}" controls muted></video><figcaption>video</figcaption></figure></div>`;
  return `<div class="post"><div class="chain">
    <b class="sig">signal #${p.signal_id}</b>→<b class="bri">brief #${p.brief_id}</b>→
    <b class="ast">${(p.assets||[]).length} assets</b>→<b class="pub">${esc(p.platform)}</b>
    <span class="badge ${p.status}">${p.status}</span>
    <a style="margin-left:auto" href="#/cycle/${p.cycle_id}">cycle #${p.cycle_id} →</a></div>
    <div class="cap">${esc(p.post_text)}</div>${imgs}
    <div class="meta">signal: <b>${esc(p.topic)}</b> [${esc(p.signal_type)}] — ${esc(p.summary)}</div>
    <div class="meta">why now: ${esc(p.why_now)}</div>
    <div class="meta">score ${p.score} · velocity ${p.velocity} · fit ${p.niche_fit} ·
      producibility ${p.producibility} · ${p.source_count} sources</div>
    <div class="meta">${p.post_url?`<a href="${esc(p.post_url)}" target="_blank">${esc(p.post_url)}</a>`:"<i>not published (dry run)</i>"}</div></div>`;
}

// ── screens ─────────────────────────────────────────────────────
const Screens={
overview:{render(){
  if(!S||!F)return'<div class="empty">loading…</div>';
  const ps=F.personas,c={active:0,warming:0,paused:0,suspended:0};
  let posts=0,accts=0;
  for(const p of ps)for(const a of legsOf(p)){
    c[a.status]=(c[a.status]||0)+1; posts+=a.posts_today||0; accts++}
  const exc=ps.filter(needsAttention);
  const badCount=ps.reduce((s,p)=>s+badLegs(p).length,0);
  const latest=(S.cycles||[])[0];
  const prov=(S.providers||[]).map(x=>{
    const cls=x.ok?((x.note&&!x.info)?"warn":"ok"):(x.planned?"plan":"bad");
    const txt=x.ok?(x.note?esc(x.note):"connected"):esc(x.missing||"key missing");
    return `<div class="card"><div class="t"><span class="dot ${cls}"></span>${esc(x.name)}</div>
      <div class="n">${esc(x.role)}</div><div class="n">${txt}</div></div>`}).join("");
  const demo=ps.some(p=>p.demo)?'<span class="demob">includes seeded demo data</span>':"";
  return `<div class="crumb">autoStudio</div>
  <h1>Overview ${demo}<span class="clock">updated ${S.now}</span></h1>
  <div class="statrow">
    <div class="stat"><div class="v">${ps.length}</div><div class="t">personas</div></div>
    <div class="stat"><div class="v">${accts}</div><div class="t">accounts</div></div>
    <div class="stat good"><div class="v">${c.active}</div><div class="t">active</div></div>
    <div class="stat"><div class="v">${c.warming}</div><div class="t">warming up</div></div>
    <div class="stat"><div class="v">${c.paused}</div><div class="t">paused</div></div>
    <div class="stat attn"><div class="v">${c.suspended}</div><div class="t">suspended</div></div>
    <div class="stat"><div class="v">${posts}</div><div class="t">posts today</div></div>
    <div class="stat ${badCount?"attn":"good"}"><div class="v">${badCount}</div><div class="t">accounts need attention</div></div>
  </div>
  <h2>Needs attention <a href="#/personas">all personas →</a></h2>
  ${badCount?exc.slice(0,6).map(excHTML).join(""):'<div class="empty">nothing needs you — the fleet is healthy</div>'}
  ${badCount>6?`<div class="meta">+ ${badCount-6} more in <a href="#/personas">Personas</a></div>`:""}
  <h2>Latest cycle <a href="#/pipeline">pipeline →</a></h2>
  ${latest?pipeBoxHTML(latest,true):'<div class="empty">no cycles yet — run <code>python run.py</code></div>'}
  <h2>Providers</h2><div class="cards3">${prov}</div>`;
}},
pipeline:{render(){
  if(!S)return'<div class="empty">loading…</div>';
  const cs=S.cycles||[];
  const hist=cs.slice(1).map(c=>{
    const sig=c.events.find(e=>e.stage==="signals"&&e.status==="done");
    return `<div class="rowitem"><b>cycle #${c.id}</b>
      <span>${T(c.started_at)} UTC</span><span class="badge ${c.status}">${c.status}</span>
      <span>${c.raw_item_count??0} raw</span>
      ${sig?`<span>${esc(sig.detail.slice(0,60))}</span>`:""}
      <a href="#/cycle/${c.id}">detail →</a></div>`}).join("");
  return `<div class="crumb">autoStudio</div>
  <h1>Pipeline <span class="clock">updated ${S.now}</span></h1>
  ${cs.length?pipeBoxHTML(cs[0],false):'<div class="empty">no cycles yet</div>'}
  <h2>Cycle history</h2>${hist||'<div class="empty">no earlier cycles</div>'}
  <h2>Studio totals</h2>
  <div class="statrow">${[["cycles run",S.stats.cycles],["raw items seen",S.stats.raw_items],
    ["signals typed",S.stats.signals],["assets generated",S.stats.assets],
    ["posts published",S.stats.posts_published],["dry runs",S.stats.posts_dry]]
    .map(([l,v])=>`<div class="stat"><div class="v">${v??0}</div><div class="t">${l}</div></div>`).join("")}</div>`;
}},
signals:{render(){
  if(!PL)return'<div class="empty">loading the signal pool…</div>';
  const cats=PL.categories||[];
  const fresh=cats.reduce((s,c)=>s+c.kept,0);
  const expired=cats.reduce((s,c)=>s+c.expired,0);
  const stale=cats.filter(c=>c.stale).length;
  const rows=cats.map(c=>{
    const top=c.signals[0];
    const open=PLopen===c.category;
    const badge=c.stale
      ?`<span class="badge failed">stale · ${Math.round(c.age_hours)}h old</span>`
      :`<span class="badge published">harvested ${c.age_hours}h ago</span>`;
    let body="";
    if(open){
      const srcs=Object.entries(c.sources||{}).filter(([,v])=>v)
        .map(([k,v])=>`${esc(k)} ${v}`).join(" · ");
      const sigs=c.signals.map(s=>{
        const left=Math.max(0,Math.round(s.expiry_hours-c.age_hours));
        const ttl=left>=72?`${Math.round(left/24)}d left`:`${left}h left`;
        const links=(s.exemplar_urls||[]).map((u,i)=>
          ` · <a href="${esc(u)}" target="_blank">src${i+1}</a>`).join("");
        return `<div class="card sig">
          <div class="tt">${esc(s.topic)} <small>[${esc(s.signal_type)}] score ${s.score}</small></div>
          <div class="meta">${esc(s.summary)}</div>
          <div class="meta">why now: ${esc(s.why_now)}</div>
          <div class="meta">velocity ${s.velocity} · fit ${s.niche_fit} · producibility ${s.producibility}
            · ${s.source_count} sources · <b>${ttl}</b>${links}</div></div>`}).join("")
        ||'<div class="empty">no fresh signals — every wave expired</div>';
      body=`<div class="meta" style="margin:2px 0 8px">source yield: ${srcs||"—"}
        <span style="float:right">raw items counted upstream: ${c.raw_item_count} (never stored)</span></div>${sigs}`;
    }
    return `<div class="rowitem" style="cursor:pointer" onclick="PLtoggle('${c.category}')">
      <b>${esc(c.category)}</b> ${badge}
      <span>${c.kept} fresh${c.expired?` · ${c.expired} expired`:""}</span>
      ${top?`<span>top: ${esc(top.topic)} (${top.score})</span>`:"<span>empty pool</span>"}
      <span style="margin-left:auto">${open?"▾ close":"▸ signals"}</span></div>${body}`;
  }).join("");
  const issues=(PL.source_issues||[]).map(i=>`<div>${esc(i)}</div>`).join("");
  return `<div class="crumb">autoStudio</div>
  <h1>Signals <span class="clock">pool ${esc(PL.snapshot||"—")} ·
    <a onclick="PL=null;show()" style="cursor:pointer">reload</a></span></h1>
  <div class="meta" style="margin-bottom:10px">The shared pool the trend-harvest routine
    commits twice a day — every publishing account draws from its own category here.</div>
  <div class="statrow">
    <div class="stat"><div class="v">${cats.length}</div><div class="t">category pools</div></div>
    <div class="stat good"><div class="v">${fresh}</div><div class="t">fresh signals</div></div>
    <div class="stat"><div class="v">${expired}</div><div class="t">expired (auto-dropped)</div></div>
    <div class="stat ${stale?"attn":"good"}"><div class="v">${stale}</div><div class="t">stale pools</div></div>
  </div>
  ${rows}
  <h2>Source health — what the harvest flagged</h2>
  ${issues?`<div class="evlog">${issues}</div>`
    :'<div class="empty">no source issues reported by the last harvest</div>'}`;
},async load(){
  try{const r=await fetch("/api/pool");if(r.ok){PL=await r.json();show()}}catch(e){}
}},
performance:{render(){
  if(!PM)return'<div class="empty">reading platform metrics…</div>';
  const accts=PM.accounts||[];
  const stat=accts.map(a=>{
    const hist=a.history||[];
    const first=hist.find(h=>h.followers!==null&&h.followers!==undefined);
    const delta=(first&&a.followers!==null&&a.followers!==undefined)
      ?a.followers-first.followers:null;
    const label=`${esc(a.persona)} · ${esc(a.platform)}`;
    if(a.status==="suspended")return `<div class="stat attn"><div class="v">✗</div>
      <div class="t">${label} SUSPENDED — appeal or re-provision</div></div>`;
    if(a.status!=="ok")return `<div class="stat"><div class="v">—</div>
      <div class="t">${label}: ${esc(a.status)}</div></div>`;
    return `<div class="stat good"><div class="v">${a.followers??"?"}</div>
      <div class="t">${label} followers${delta!==null&&delta!==0
        ?` (${delta>0?"+":""}${delta} since tracking)`:""}
        · ${hist.length} captures in ledger</div></div>`;
  }).join("");
  const rows=accts.flatMap(a=>(a.posts||[]).map(p=>{
    const eng=p.views!==null&&p.views!==undefined?`${p.views} views`
      :`${p.likes??0}♥ ${p.reposts??0}↻ ${p.replies??0}💬`;
    return `<div class="rowitem">
      <span style="display:inline-flex;align-items:center;gap:6px">${platIcon(a.platform,13)}${esc(a.persona)}</span>
      <span>${esc(p.created_at||"")}</span>
      <b>${eng}</b>
      <span>${p.topic?esc(p.topic):'<i style="color:var(--faint)">pre-studio post</i>'}</span>
      ${p.url?`<a style="margin-left:auto" href="${esc(p.url)}" target="_blank">open →</a>`:""}
    </div>`})).join("");
  return `<div class="crumb">autoStudio</div>
  <h1>Performance <span class="clock">captured ${esc((PM.captured_at||"").slice(11,16))} UTC ·
    <a onclick="PM=null;show()" style="cursor:pointer">reload</a></span></h1>
  <div class="meta" style="margin-bottom:10px">The feedback half of the loop — what each platform
    did with our posts. The trend ledger lives in git (data/metrics/), written by the
    shared metrics harvest twice a day; this view fetches live numbers on top of it.</div>
  <div class="statrow">${stat}</div>
  <h2>Per-post engagement</h2>
  ${rows||'<div class="empty">no readable posts yet — publish somewhere measurable</div>'}`;
},async load(){
  try{const r=await fetch("/api/performance");if(r.ok){PM=await r.json();show()}}catch(e){}
}},
personas:{render(){
  if(!F)return'<div class="empty">loading…</div>';
  const ps=F.personas;
  const plat={};for(const p of ps)for(const a of legsOf(p))
    plat[a.platform]=(plat[a.platform]||0)+1;
  const exc=ps.filter(needsAttention);
  const demo=ps.some(p=>p.demo)?'<span class="demob">includes seeded demo data</span>':"";
  const accts=ps.reduce((s,p)=>s+legsOf(p).length,0);
  return `<div class="crumb">autoStudio</div>
  <h1>Personas ${demo}<span class="clock">${ps.length} personas · ${accts} accounts</span></h1>
  <div class="chips">${Object.entries(plat).sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<span style="display:inline-flex;align-items:center;gap:6px">${platIcon(k,13)}${esc(k)} · ${v}</span>`).join("")}</div>
  <h2>Exception queue — the only list you actually manage</h2>
  <div id="excwrap">${exc.length?exc.map(excHTML).join(""):'<div class="empty">fleet is healthy</div>'}</div>
  <h2>All personas</h2>
  <div class="filters">
    <input id="q" placeholder="search name / handle / niche…" value="${esc(PQ)}">
    ${["all","attention","active","warming","paused"].map(f=>
      `<button class="fbtn ${PF===f?"on":""}" data-f="${f}">${f==="attention"?"needs attention":f}</button>`).join("")}
  </div>
  <div class="grid" id="pgrid"></div>`;
},after(){
  const grid=()=>{
    const q=PQ.toLowerCase();
    let rows=F.personas.filter(p=>{
      const hay=(p.name+p.niche+legsOf(p).map(a=>a.platform+a.handle).join("")).toLowerCase();
      if(q&&!hay.includes(q))return false;
      if(PF==="all")return true;
      if(PF==="attention")return needsAttention(p);
      return legsOf(p).some(a=>a.status===PF)});
    const rank={suspended:0,warming:2,paused:3,active:4};
    rows.sort((a,b)=>(needsAttention(a)?0:1)-(needsAttention(b)?0:1)
      ||rank[worstStatus(a)]-rank[worstStatus(b)]||a.name.localeCompare(b.name));
    document.getElementById("pgrid").innerHTML=rows.map(p=>{
      const legs=legsOf(p);
      const total=legs.reduce((s,a)=>s+(a.posts_today||0),0);
      const legRows=legs.map(a=>`<div class="leg ${a.status}">
        <span class="pl">${platIcon(a.platform)}${esc(a.platform)}</span>
        <span>${a.posts_today??0} today</span>
        <span class="st">${a.status==="active"
          ? ago(a.last_post_at)
          : `<span class="dot ${a.status}"></span> ${esc(a.status)}`}</span></div>`).join("");
      return `<div class="tile ${worstStatus(p)}" title="click to manage"
           onclick="location.hash='#/persona/${p.id}'">
        ${p.demo?'<span class="d">demo</span>':""}
        <div class="n"><span class="dot ${worstStatus(p)}"></span>${esc(p.name)}</div>
        <div class="h">${esc(p.niche)} · ${legs.length} account${legs.length>1?"s":""}</div>
        <div class="m">${total} posts today</div>
        <div class="legs">${legRows}</div>
      </div>`}).join("")||'<div class="empty">no personas match</div>';
  };
  document.getElementById("q").oninput=e=>{PQ=e.target.value;grid()};
  document.querySelectorAll(".fbtn").forEach(b=>b.onclick=()=>{
    PF=b.dataset.f;
    document.querySelectorAll(".fbtn").forEach(x=>x.classList.toggle("on",x===b));grid()});
  grid();
}},
content:{render(){
  if(!S)return'<div class="empty">loading…</div>';
  const posts=S.lineage||[];
  return `<div class="crumb">autoStudio</div>
  <h1>Content <span class="clock">${posts.length} posts with full lineage</span></h1>
  ${posts.map(postHTML).join("")||'<div class="empty">no posts yet — run <code>python run.py</code></div>'}`;
}},
assets:{render(){
  if(!S)return'<div class="empty">loading…</div>';
  const items=(S.assets||[]).filter(x=>x.kind!=="audio");
  return `<div class="crumb">autoStudio</div>
  <h1>Assets <span class="clock">green border = judge pick</span></h1>
  <div class="gal">${items.map(x=>{
    const src="/asset?p="+encodeURIComponent(x.path);
    const media=x.kind==="video"
      ?`<video src="${src}" muted loop onmouseover="this.play()" onmouseout="this.pause()"
         style="border:2px solid ${x.chosen?"var(--teal)":"transparent"}"></video>`
      :`<img src="${src}" style="border:2px solid ${x.chosen?"var(--teal)":"transparent"}">`;
    return `<figure>${media}<figcaption>${esc(x.topic||"")} · ${esc(x.kind)}</figcaption></figure>`
  }).join("")||'<div class="empty">nothing generated yet</div>'}</div>`;
}},
persona:{render(arg){
  if(!PD||PDid!==String(arg))return'<div class="empty">loading persona…</div>';
  const p=PD.persona,cat=PD.catalog,accts=p.accounts||[];

  // one diagnosis block per platform account
  const blocks=accts.map(a=>{
    const d=a.diagnosis||{};
    const acts=(d.remedies||[]).filter(x=>x!=="note").map(x=>{
      const [label]=cat[x]||[x];
      const warn=(x==="run_cycle_live"||x==="pause")?" warn":"";
      return `<button class="abtn${warn}" onclick="act(${p.id},'${x}','${a.platform}',this)">${esc(label)}</button>`
    }).join("");
    const steps=(d.human_steps||[]).length
      ?`<div class="meta" style="margin-top:10px"><b>Human steps (only you can do these):</b></div>
        <ol class="steps">${d.human_steps.map(s=>`<li>${esc(s)}</li>`).join("")}</ol>`:"";
    const posts=(a.recent_posts||[]).map(x=>
      `<div class="acctrow"><span class="badge ${x.status}">${x.status}</span>
        <span>${(x.posted_at||"").slice(0,16).replace("T"," ")}</span>
        ${x.url?`<a href="${esc(x.url)}" target="_blank">open →</a>`:""}</div>`).join("");
    return `<h2 style="display:flex;align-items:center;gap:7px">${platIcon(a.platform,15)}${esc(a.platform)} <span class="badge ${a.status}">${a.status}</span></h2>
      <div class="card diag ${d.severity||"ok"}">
        <div class="meta">${esc(at(a.handle||"—"))} · cadence ${esc(a.cadence||"—")} ·
          ${a.posts_today??0} today · last post ${ago(a.last_post_at)}</div>
        <div class="sy" style="margin-top:8px">${esc(d.symptom||"—")}</div>
        ${d.cause?`<div class="meta">${esc(d.cause)}</div>`:""}
        <div class="acts">${acts}</div>
        ${steps}
        ${posts?`<div class="meta" style="margin-top:10px"><b>recent posts:</b></div>${posts}`:""}
      </div>`}).join("");

  const anyPlat=accts.length?accts[0].platform:"";
  const extra=["run_cycle","run_cycle_live","clear_session","reset_warmup","recheck","pause","resume"]
    .map(x=>{const [label]=cat[x]||[x];
      return `<button class="abtn" onclick="act(${p.id},'${x}','${anyPlat}',this)">${esc(label)}</button>`}).join("");
  const hist=(p.actions||[]).map(a=>
    `<div>[${a.created_at.slice(0,16).replace("T"," ")}] <b>${esc(a.action)}</b> — ${esc(a.detail)}</div>`).join("");

  return `<div class="crumb"><a href="#/personas">personas</a> / manage</div>
  <h1>${esc(p.name)} ${p.demo?'<span class="demob">demo persona</span>':""}
    <span class="clock">${accts.length} account${accts.length>1?"s":""}</span></h1>
  <div class="card"><div class="meta">${esc(p.niche)} ·
    ${accts.map(a=>`${platIcon(a.platform,13)} ${esc(a.platform)} ${esc(at(a.handle))}`).join(" &nbsp;·&nbsp; ")}</div></div>
  ${blocks||'<div class="empty">no accounts linked</div>'}
  <h2>All actions <span style="text-transform:none;letter-spacing:0">(applies to
    ${esc(anyPlat||"persona")})</span></h2>
  <div class="card"><div class="acts">${extra}</div>
    <div class="noteform">
      <input id="noteinp" placeholder="add an operator note (e.g. appeal reference)…">
      <button class="abtn" onclick="act(${p.id},'note',document.getElementById('noteinp').value,this)">Add note</button>
    </div></div>
  <h2>Action log — audit trail</h2>
  <div class="log">${hist||"no actions recorded yet"}</div>`;
},load(arg){loadPersona(arg)}},
cycle:{render(arg){
  const c=CD[arg];
  if(!c)return'<div class="empty">loading cycle #'+esc(arg)+'…</div>';
  const span={};
  for(const e of c.events){const k=e.stage;(span[k]??={a:e.created_at}).b=e.created_at}
  const durs=Object.entries(span).map(([k,v])=>{
    const ms=new Date(v.b)-new Date(v.a);
    return `<span>${k} ${ms>=60000?Math.floor(ms/60000)+"m"+Math.round(ms%60000/1000)+"s":Math.round(ms/1000)+"s"}</span>`}).join("");
  const coll=c.events.filter(e=>e.stage==="collect"&&e.status==="progress");
  let h=`<div class="crumb"><a href="#/pipeline">pipeline</a> / cycle</div>
  <h1>Cycle #${c.id} <span class="badge ${c.status}">${c.status}</span></h1>
  <div class="card"><div class="meta">started <b>${T(c.started_at)}</b> UTC ·
    finished <b>${T(c.finished_at)||"—"}</b> · <b>${c.raw_item_count??0}</b> raw items
    ${c.note?`· note: ${esc(c.note)}`:""}</div><div class="durs">${durs}</div></div>`;
  if(coll.length)h+=`<h2>Collect — per source</h2><div class="card"><div class="chips">`+
    coll.map(e=>`<span>${esc(e.detail)}</span>`).join("")+`</div></div>`;
  if(c.signals.length){h+=`<h2>Signals — all ${c.signals.length} typed this cycle</h2>`;
    for(const s of c.signals)h+=`<div class="card sig ${s.chosen?"chosen":""}">
      <div class="tt">${s.chosen?"★ ":""}${esc(s.topic)} <small>[${esc(s.signal_type)}] score ${s.score}</small></div>
      <div class="meta">${esc(s.summary)}</div>
      <div class="meta"><b>why now:</b> ${esc(s.why_now)}</div>
      <div class="meta">velocity ${s.velocity} · fit ${s.niche_fit} · producibility ${s.producibility}
        · expires ${s.expiry_hours}h · ${s.source_count} sources
        ${(s.exemplar_urls||[]).slice(0,3).map((u,i)=>` · <a href="${esc(u)}" target="_blank">src${i+1}</a>`).join("")}</div></div>`}
  for(const b of c.briefs||[]){
    h+=`<h2>Brief #${b.id} — ${esc(b.format)}</h2><div class="card">
      <div class="meta"><b>premise:</b> ${esc(b.premise)}</div>
      <div class="meta"><b>angle:</b> ${esc(b.angle)}</div>
      <div class="meta"><b>mood:</b> ${esc(b.mood)} · <b>model:</b> ${esc(b.model)}</div>
      <div class="meta"><b>caption:</b></div><pre>${esc(b.caption)}</pre>
      <div class="meta"><b>alt text:</b> ${esc(b.alt_text)}</div>
      ${b.voiceover_script?`<div class="meta"><b>voiceover:</b></div><pre>${esc(b.voiceover_script)}</pre>`:""}
      <div class="meta"><b>image prompts (as sent to the renderer):</b></div>
      ${b.image_prompts.map((p,i)=>`<pre>${i}: ${esc(p)}</pre>`).join("")}`;
    if(b.assets.length)h+=`<div class="imgs">`+b.assets.map(a=>{
      const src="/asset?p="+encodeURIComponent(a.path);
      const tag=a.kind==="video"?`<video src="${src}" controls muted></video>`
        :a.kind==="audio"?`<audio src="${src}" controls style="width:160px"></audio>`:`<img src="${src}">`;
      return `<figure class="${a.chosen?"chosen":""}">${tag}
        <figcaption>${a.chosen?"✓ ":""}${esc(a.kind)} · ${esc(a.model)}
        ${a.meta.judge_reason?`<br>judge: ${esc(a.meta.judge_reason)}`:""}</figcaption></figure>`}).join("")+`</div>`;
    for(const p of b.posts||[])h+=`<div class="meta"><b>post:</b>
      <span class="badge ${p.status}">${p.status}</span>
      ${p.url?` <a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a>`:""} · ${T(p.posted_at)}</div>`;
    h+=`</div>`}
  h+=`<h2>Event timeline</h2><div class="evlog" style="max-height:none">`+
    c.events.map(e=>`<div class="${e.status==='failed'?'f':''}">[${T(e.created_at)}] ${e.stage} · ${e.status} — ${esc(e.detail)}</div>`).join("")+`</div>`;
  return h;
},async load(arg){
  try{const r=await fetch("/api/cycle?id="+arg);if(r.ok){CD[arg]=await r.json();show()}}catch(e){}
}}};

// ── router + data loop ──────────────────────────────────────────
function show(){
  const {s,arg}=cur();
  document.getElementById("nav").innerHTML=navHTML();
  document.getElementById("sideinfo").innerHTML=sideHTML();
  const scr=Screens[s]||Screens.overview;
  document.getElementById("main").innerHTML=scr.render(arg);
  if(scr.after)scr.after(arg);
  if(s==="cycle"&&!CD[arg])Screens.cycle.load(arg);
  if(s==="persona"&&(!PD||PDid!==String(arg)))Screens.persona.load(arg);
  if(s==="signals"&&!PL)Screens.signals.load();
  if(s==="performance"&&!PM)Screens.performance.load();
}
window.addEventListener("hashchange",show);
async function refresh(){
  try{S=await (await fetch("/api/state")).json()}catch(e){}
  try{F=await (await fetch("/api/fleet")).json()}catch(e){}
  const {s,arg}=cur();
  // don't stomp the personas search box while typing — update grid only
  if(s==="personas"&&document.getElementById("pgrid")){Screens.personas.after();
    document.getElementById("nav").innerHTML=navHTML();
    document.getElementById("sideinfo").innerHTML=sideHTML()}
  else if(s==="cycle"){if(CD[arg]&&CD[arg].status==="running")Screens.cycle.load(arg);
    document.getElementById("nav").innerHTML=navHTML()}
  else if(s==="persona"){document.getElementById("nav").innerHTML=navHTML();
    document.getElementById("sideinfo").innerHTML=sideHTML()}
  else show();
  const running=S&&S.cycles&&S.cycles[0]&&S.cycles[0].status==="running";
  setTimeout(refresh,running?2500:6000);
}
refresh();
// a fresh load that already carries a hash (bookmark, shared link, F5 on a
// drill-down) never fires hashchange — paint the route once explicitly, after
// the first refresh() has had a moment to seed S/F
if(location.hash)setTimeout(show,150);
</script></body></html>"""

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".mp4": "video/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The console is a live tool whose HTML+JS changes as the project does —
        # a cached page silently shows a stale UI, which reads as "my change
        # didn't work". Assets are content-addressed by path, so only cache those.
        if not self.path.startswith("/asset"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("/", "/fleet", "/cycle"):
                self._send(200, "text/html; charset=utf-8", APP.encode())
            elif parsed.path == "/api/state":
                self._send(200, "application/json", json.dumps(state()).encode())
            elif parsed.path == "/api/fleet":
                self._send(200, "application/json", json.dumps(fleet_state()).encode())
            elif parsed.path == "/api/pool":
                self._send(200, "application/json", json.dumps(pool_state()).encode())
            elif parsed.path == "/api/performance":
                self._send(200, "application/json",
                           json.dumps(performance_state()).encode())
            elif parsed.path == "/api/persona":
                pid = int(parse_qs(parsed.query).get("id", ["0"])[0])
                detail = persona_state(pid)
                if detail is None:
                    self._send(404, "application/json", b'{"error":"not found"}')
                else:
                    self._send(200, "application/json", json.dumps(detail).encode())
            elif parsed.path == "/api/cycle":
                cid = int(parse_qs(parsed.query).get("id", ["0"])[0])
                detail = store.cycle_detail(store.connect(), cid)
                if detail is None:
                    self._send(404, "application/json", b'{"error":"not found"}')
                else:
                    self._send(200, "application/json", json.dumps(detail).encode())
            elif parsed.path == "/asset":
                p = Path(parse_qs(parsed.query).get("p", [""])[0]).resolve()
                if ASSETS_DIR.resolve() in p.parents and p.exists():
                    self._send(200, MIME.get(p.suffix.lower(), "application/octet-stream"),
                               p.read_bytes())
                else:
                    self._send(404, "text/plain", b"not found")
            else:
                self._send(404, "text/plain", b"not found")
        except BrokenPipeError:
            pass

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/action":
            self._send(404, "application/json", b'{"ok":false,"message":"not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            result = remediation.apply(store.connect(), int(body.get("persona_id", 0)),
                                       str(body.get("action", "")),
                                       str(body.get("payload", "")))
            self._send(200, "application/json", json.dumps(result).encode())
        except Exception as e:
            self._send(200, "application/json",
                       json.dumps({"ok": False, "message": str(e)[:200]}).encode())


if __name__ == "__main__":
    print(f"ops console → http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
