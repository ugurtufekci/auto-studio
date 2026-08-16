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
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from studio import version as _version  # noqa: E402
from studio import (  # noqa: E402
    approvals,
    draftpool,
    health,
    metrics,
    persona,
    pool,
    remediation,
    store,
)

ASSETS_DIR = ROOT / "assets"
PORT = 8377


def state() -> dict:
    """Everything the console needs, shaped by who has to act on it:
    the inbox (operator), account cards (per-account health incl. publish
    gate), shared services (fleet-wide), machine keys (deployment fact).
    The old single 'providers' list conflated all four — see studio/health.py."""
    con = store.connect()
    cards = health.account_cards(con)
    return {
        "attention": health.attention(con, cards),
        "accounts": cards,
        "services": health.shared_services(con),
        "machine": health.machine_keys(),
        "cycles": store.cycles_with_events(con, limit=10),
        "stats": store.stats(con),
        "lineage": store.lineage(con)[:15],
        "assets": store.recent_assets(con, limit=30),
        "drafts_pending": len(draftpool.pending()),
        "code_running": RUNNING_CODE,
        "code_on_disk": _version.code_version(),
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
        index = json.loads((pool.POOL_DIR / "index.json").read_text(encoding="utf-8"))
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


CAPTION_LIMITS = {"instagram": 2200, "telegram": 1024, "mastodon": 500,
                  "bluesky": 300, "youtube": 5000}


def drafts_state() -> dict:
    """The approval queue from the git ledger, each draft carrying the EXACT
    text the platform would receive — composed through the same function the
    adapters use, so what the operator approves is what gets published, to
    the character. Drafts made on another machine appear after a git pull;
    their media rides in the ledger too."""
    from studio import draftpool
    from studio.publisher import compose_plain
    items = []
    for d in draftpool.pending():
        try:
            final_text = compose_plain(
                d.get("text", ""), CAPTION_LIMITS.get(d["platform"], 1000),
                d.get("provenance"), d.get("persona"),
                max_hashtags=5 if d["platform"] == "instagram" else None)
        except Exception:
            final_text = d.get("text", "")
        local = draftpool.media_path(d)
        remote = (d.get("provenance") or {}).get("source_url", "")
        slides = draftpool.media_paths(d)
        cover = draftpool.MEDIA_DIR / (d.get("cover_file") or "")
        items.append({**d, "final_text": final_text,
                      "cover_local": str(cover) if d.get("cover_file")
                      and cover.exists() else "",
                      "media_local": str(local) if local else "",
                      "media_slides": [str(x) for x in slides] if len(slides) > 1 else [],
                      "media_remote": remote})
    history = []
    for d in draftpool.resolved():
        local = draftpool.media_path(d)
        history.append({
            "id": d.get("id"), "persona": d.get("persona"),
            "platform": d.get("platform"), "status": d.get("status"),
            "note": d.get("note", ""), "resolved_at": d.get("resolved_at", ""),
            "text": d.get("text", ""), "media_kind": d.get("media_kind", ""),
            "media_local": str(local) if local else "",
            "url": (d.get("note", "") if d.get("status") == "approved"
                    and str(d.get("note", "")).startswith("http") else ""),
        })
    return {"drafts": items, "history": history,
            "now": time.strftime("%H:%M:%S")}


def _read_json_file(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def accounts_state() -> dict:
    """One row per account leg — the whole fleet as one sortable table.

    Cards scale to a dozen accounts; a fleet of a hundred personas needs the
    density of a table, with the state's REASON inline so nothing critical
    hides behind a drill-in."""
    from studio import draftpool
    con = store.connect()
    ledger = draftpool.resolved(limit=500)
    pending = draftpool.pending()
    rows = []
    for c in health.account_cards(con):
        key = (c["persona"], c["platform"])
        latest = _read_json_file(
            metrics.METRICS_DIR / f"{c['platform']}--{c['handle']}" / "latest.json")
        last_url, last_at = "", ""
        for d in ledger:
            if ((d.get("persona"), d.get("platform")) == key
                    and d.get("status") == "approved"):
                note = str(d.get("note") or "")
                last_url = note if note.startswith("http") else ""
                last_at = d.get("resolved_at", "")
                break
        rows.append({**c,
                     "followers": latest.get("followers")
                     or latest.get("subscribers"),
                     "pending": sum(1 for d in pending
                                    if (d.get("persona"),
                                        d.get("platform")) == key),
                     "last_post_url": last_url, "last_post_at": last_at})
    return {"accounts": rows}


def account_detail(leg: str) -> dict | None:
    """Everything about one account leg: state with its reason, the metric
    trail, and the ledger's post history — the operator's per-account page."""
    from studio import draftpool
    con = store.connect()
    card = next((c for c in health.account_cards(con)
                 if f"{c['platform']}--{c['handle']}" == leg), None)
    if card is None:
        return None
    mdir = metrics.METRICS_DIR / leg
    series = []
    hpath = mdir / "history.jsonl"
    if hpath.exists():
        for line in hpath.read_text(encoding="utf-8").splitlines()[-90:]:
            try:
                series.append(json.loads(line))
            except Exception:
                continue
    key = (card["persona"], card["platform"])
    history = [{"id": d.get("id"), "status": d.get("status"),
                "note": d.get("note", ""), "when": d.get("resolved_at", ""),
                "text": (d.get("text") or "")[:200]}
               for d in draftpool.resolved(limit=500)
               if (d.get("persona"), d.get("platform")) == key][:40]
    pend = [{"id": d.get("id"), "when": d.get("created_at", "")}
            for d in draftpool.pending()
            if (d.get("persona"), d.get("platform")) == key]
    return {"card": card, "series": series,
            "latest": _read_json_file(mdir / "latest.json"),
            "history": history, "pending": pend}


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
/* the verdict banner - the one sentence Overview exists to answer */
.verdict{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;border-radius:12px;
padding:13px 16px;margin-bottom:6px;font-size:14.5px;border:1px solid}
.verdict.ok{background:rgba(29,158,117,.08);border-color:var(--teal);color:var(--tealt)}
.verdict.warn{background:rgba(186,117,23,.10);border-color:var(--amber);color:var(--ambert)}
.verdict.bad{background:rgba(226,75,74,.10);border-color:var(--red);color:var(--redt)}
.verdict b{font-size:15px} .verdict .sub{color:var(--muted);font-size:12.5px}
.sev{font-size:9.5px;letter-spacing:.08em;border-radius:9px;padding:1px 8px;border:1px solid;flex:none}
.sev.critical{color:var(--redt);border-color:var(--red);background:rgba(226,75,74,.14)}
.sev.action{color:var(--ambert);border-color:var(--amber)}
.sev.watch{color:var(--faint);border-color:var(--hair)}
.due{margin-left:auto;color:var(--ambert);font-size:11.5px;white-space:nowrap}
.due.tight{color:var(--redt);font-weight:600}
.syscols{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media(max-width:1020px){.syscols{grid-template-columns:1fr}}
.krow{display:flex;gap:9px;align-items:baseline;font-size:12px;color:var(--muted);
padding:5px 0;border-top:1px solid var(--hair)}
.krow:first-of-type{border-top:0}
.krow b{color:var(--ink);font-weight:500;min-width:72px}
.krow .sv{margin-left:auto;color:var(--faint);font-size:11px;text-align:right}
.gate{font-size:10.5px;color:var(--faint)} .gate.open{color:var(--tealt)}
.card{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:14px 16px}
.cards3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.card .t{font-size:12px;color:var(--faint)} .card .n{font-size:12px;color:var(--muted);margin-top:2px}
.badge{font-size:10px;padding:1px 8px;border-radius:10px;border:1px solid var(--hair);color:var(--muted)}
.badge.published{color:var(--tealt);border-color:var(--teal)}
.badge.failed{color:var(--redt);border-color:var(--red)}
.badge.dry_run,.badge.running,.badge.queued,.badge.pending{color:var(--ambert);border-color:var(--amber)}
/* approval queue - a held post shown exactly as it would publish */
.appr{background:var(--panel);border:1px solid var(--amber);border-radius:14px;
padding:16px 18px;margin-bottom:14px;display:grid;grid-template-columns:minmax(180px,260px) 1fr;
gap:16px}
/* The media box keeps its size before the file has loaded. Without a
   reserved box a video is 300×150 until its metadata arrives and an image is
   nothing at all, so every card in the queue collapses and re-expands — the
   page shrinks under the reader, the browser clamps the scroll position to
   the shorter document, and someone reading down the queue is thrown back to
   the top. contain, not cover: a 9:16 reel and a square post both show whole
   inside the same box. */
/* a carousel scrolls sideways inside its own card, like the post will */
.slides{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.slides figure{margin:0;flex:0 0 62%;scroll-snap-align:start;position:relative}
.slides figcaption{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.6);
  color:#fff;font-size:11px;padding:2px 7px;border-radius:20px}
.slides img{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:10px;display:block}
.appr .med img,.appr .med video{width:100%;aspect-ratio:4/5;object-fit:contain;
  background:rgba(0,0,0,.05);border-radius:10px;display:block}
.appr .who{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--muted);
flex-wrap:wrap;margin-bottom:8px}
.appr .who b{color:var(--ink);font-size:14px}
.appr .final{font:13.5px/1.65 ui-monospace,monospace;background:var(--panel2);
border:1px solid var(--hair);border-radius:10px;padding:12px 14px;white-space:pre-wrap;
color:var(--ink)}
.appr .final .cap-note{display:block;font-size:10px;color:var(--faint);
letter-spacing:.08em;text-transform:uppercase;margin-bottom:7px;font-family:-apple-system,sans-serif}
.appr .altrow{font-size:11.5px;color:var(--faint);margin-top:8px}
/* ledger history — the cross-machine record of what actually went out */
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}
.tiles .tile{display:flex;flex-direction:column;gap:3px;padding:16px 18px;
  border-radius:12px;background:var(--panel);border:1px solid var(--line);
  text-decoration:none;cursor:pointer}
.tiles .tile b{font-size:26px;color:var(--ink);font-variant-numeric:tabular-nums}
.tiles .tile span{font-size:12px;color:var(--muted)}
.tiles .tile.warn{border-color:var(--amber)}
.tiles .tile.bad{border-color:var(--redt,#e05e5e)}
@media(max-width:720px){.tiles{grid-template-columns:1fr}}
.heat{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}
.heat .cell{width:17px;height:17px;border-radius:4px;display:block;
  background:var(--panel2);border:1px solid var(--line)}
.heat .st-active{background:var(--teal);border-color:var(--teal)}
.heat .st-warming{background:var(--amber);border-color:var(--amber)}
.heat .st-paused{background:var(--muted);opacity:.45}
.heat .st-suspended{background:var(--redt,#e05e5e);border-color:var(--redt,#e05e5e)}
.heat .cell.closed{opacity:.5}
.heat .cell:hover{transform:scale(1.25)}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;padding:8px 10px;color:var(--muted);font-size:11px;
  text-transform:uppercase;letter-spacing:.04em;cursor:pointer;
  border-bottom:1px solid var(--line);white-space:nowrap;user-select:none}
.tbl th.on{color:var(--ink)}
.tbl td{padding:10px;border-bottom:1px solid var(--line);vertical-align:middle}
.tbl tbody tr{cursor:pointer}
.tbl tbody tr:hover td{background:var(--panel)}
.tbl .num{text-align:right;font-variant-numeric:tabular-nums}
.tbl a{color:var(--teal);text-decoration:none}
.inb .sub{display:block;margin-top:3px}
.inb.expander{cursor:pointer;color:var(--muted);justify-content:center;font-size:12px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px;margin:14px 0}
.fact{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px}
.fact b{display:block;font-size:17px;color:var(--ink);font-variant-numeric:tabular-nums}
.fact span{font-size:11px;color:var(--muted)}
.hist{display:flex;flex-direction:column;gap:2px}
.hrow{display:flex;gap:9px;align-items:center;padding:9px 12px;border-radius:9px;
  background:var(--panel);border:1px solid var(--line);font-size:12.5px}
.hrow b{color:var(--ink)}
.hrow .when{color:var(--faint);font-size:11.5px}
.hrow .lnk{margin-left:auto;display:flex;gap:8px;align-items:center;
  min-width:0;overflow:hidden}
.hrow .lnk a{color:var(--teal);text-decoration:none;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.hrow .lnk a:hover{text-decoration:underline}
.hrow .why{color:var(--muted);font-style:italic}
.cpy{flex-shrink:0;padding:3px 9px;border-radius:6px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel2);color:var(--muted);
  font:11px inherit}
.cpy:hover{color:var(--ink);border-color:var(--amber)}
.rjbox{margin-top:12px;padding:12px 14px;background:var(--panel2);
  border:1px solid var(--amber);border-radius:10px}
.rjbox label{display:block;font-size:12px;color:var(--muted);margin-bottom:8px}
.rjin{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:8px;
  border:1px solid var(--line);background:var(--panel);color:var(--ink);
  font:13px/1.4 inherit;outline:none}
.rjin:focus{border-color:var(--amber)}
.appr .edta{width:100%;min-height:200px;resize:vertical;box-sizing:border-box;
  font:13.5px/1.65 ui-monospace,monospace;color:var(--ink);
  background:var(--panel2);border:1px solid var(--amber);border-radius:10px;
  padding:12px 14px;outline:none}
.abtn.go{border-color:var(--teal);color:var(--tealt);font-weight:600}
.abtn.no:hover{border-color:var(--red);color:var(--redt)}
@media(max-width:720px){.appr{grid-template-columns:1fr}}
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
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(208px,1fr));gap:8px}
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
let DR=null;               // approval-queue payload
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
      headers:{"Content-Type":"application/json; charset=utf-8"},
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

const NAV=[["overview","⌂","Today"],["approvals","✓","Approvals"],
["accounts","▦","Accounts"],
["pipeline","▶","Pipeline"],
["signals","≋","Signals"],["performance","↗","Performance"],
["personas","☰","Personas"],["content","✎","Content"],["assets","▣","Assets"]];
let ACC=null,ACD=null,ACDleg=null,WOPEN=false,AQ="",ASORT="persona";
async function loadAccounts(){try{const r=await fetch("/api/accounts");
  if(r.ok){ACC=await r.json();show()}}catch(e){}}
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
  return{s:NAV.some(n=>n[0]===s)||s==="cycle"||s==="persona"||s==="account"?s:"overview",arg}}

function navHTML(){
  const {s}=cur();
  // the pill counts inbox items that demand a human — watch items don't nag
  const attn=S&&S.attention?S.attention.filter(i=>i.severity!=="watch").length:0;
  const total=F?F.personas.length:null;
  return NAV.map(([k,ic,label])=>{
    let pill="";
    if(k==="personas"&&total!==null)pill=`<span class="pill">${total}</span>`;
    if(k==="accounts"&&ACC)pill=`<span class="pill">${ACC.accounts.length}</span>`;
    if(k==="overview"&&attn)pill=`<span class="pill red">${attn}</span>`;
    if(k==="approvals"&&S&&S.drafts_pending)
      pill=`<span class="pill red">${S.drafts_pending}</span>`;
    return `<a href="#/${k}" class="${s===k?"on":""}"><span class="ic">${ic}</span>${label}${pill}</a>`
  }).join("");
}

// join fleet legs (DB view) with registry account cards (health view)
const AC=()=>{const m={};for(const c of (S&&S.accounts)||[])m[c.platform+"/"+c.handle]=c;return m};
const fmtLeft=t=>{if(!t)return"";const ms=new Date(t)-Date.now();if(ms<=0)return"now";
  const h=ms/36e5;return h<1?Math.round(h*60)+"m":h<48?Math.round(h)+"h":Math.round(h/24)+"d"};
// one line answering "why is / isn't this account posting right now"
const gateShort=c=>{if(!c)return"";const g=c.gate||{};
  if(c.status!=="active")return c.status;
  switch(g.kind){
    case"credentials":return"no key on this machine";
    case"warmup":return"warm-up · posts in "+fmtLeft(g.until);
    case"cadence":return g.posts_today+"/"+g.cap+" done · next "+fmtLeft(g.until);
    case"gap":return"paced · next "+fmtLeft(g.until);
    case"ready":return g.posts_today+"/"+g.cap+" today · ready";
    default:return g.reason||""}};

function staleHTML(){
  if(!S||!S.code_running||!S.code_on_disk)return "";
  if(S.code_running===S.code_on_disk)return "";
  return `<div class="inb attn" style="margin-bottom:12px">
    <b>this console is running older code</b> — serving
    <code>${esc(S.code_running)}</code> while the checkout is now
    <code>${esc(S.code_on_disk)}</code>. The page is built when the server
    starts, so a pulled fix only appears after you stop and restart
    <code>python3 dashboard/serve.py</code>.</div>`;
}
function sideHTML(){
  if(!S)return"";
  const all=S.accounts||[];
  // a dozen accounts read as a list; a hundred read as a summary
  if(all.length>12){
    const counts={};all.forEach(c=>counts[c.status]=(counts[c.status]||0)+1);
    const rows=Object.entries(counts).map(([k,v])=>
      `<div class="row"><span class="dot ${k==="active"?"ok":k}"></span>
       <span style="color:var(--ink)">${v}</span>
       <span style="margin-left:auto;font-size:10px">${esc(k)}</span></div>`).join("");
    const svc=(S.services||[]).map(x=>
      `<div class="row"><span class="dot ${x.ok?"ok":"bad"}"></span>${esc(x.name)}</div>`).join("");
    return `<div class="mini"><div class="nm">Fleet · ${all.length} accounts</div>${rows}
      <div class="row"><a href="#/accounts" style="color:var(--teal);font-size:11px;
        text-decoration:none">open the table →</a></div></div>
    <div class="mini">${svc}</div>`;
  }
  const rows=all.map(c=>{const g=c.gate||{};
    const cls=c.status!=="active"?c.status:(g.kind==="warmup"?"warming":"active");
    return `<div class="row"><span class="dot ${cls}"></span>${platIcon(c.platform,13)}
      <span style="color:var(--ink)">${esc(c.persona_name)}</span>
      <span style="margin-left:auto;font-size:10px">${esc(c.status!=="active"?c.status
        :g.kind==="warmup"?"warm-up "+fmtLeft(g.until):g.open?"ready":"paced")}</span></div>`}).join("");
  const svc=(S.services||[]).map(x=>
    `<div class="row"><span class="dot ${x.ok?"ok":"bad"}"></span>${esc(x.name)}</div>`).join("");
  return `<div class="mini"><div class="nm">Fleet</div>${rows||'<div class="row">no accounts</div>'}</div>
  <div class="mini">${svc}</div>`;
}

function personaTile(p,ac){
  const legs=legsOf(p);
  const total=legs.reduce((s,a)=>s+(a.posts_today||0),0);
  const rows=legs.map(a=>{const c=ac[a.platform+"/"+a.handle];const g=(c&&c.gate)||{};
    return `<div class="leg ${a.status}">
      <span class="pl">${platIcon(a.platform)}${esc(a.platform)}</span>
      <span class="gate ${g.open?"open":""}">${esc(gateShort(c)||a.status)}</span>
      <span class="st">${a.last_post_at?ago(a.last_post_at):""}</span></div>`}).join("");
  return `<div class="tile ${worstStatus(p)}" title="click to manage"
       onclick="location.hash='#/persona/${p.id}'">
    ${p.demo?'<span class="d">demo</span>':""}
    <div class="n"><span class="dot ${worstStatus(p)}"></span>${esc(p.name)}</div>
    <div class="h">${esc(p.niche)} · ${total} post${total===1?"":"s"} today</div>
    <div class="legs">${rows}</div></div>`;
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

let ED=null;                // draft id whose caption is being edited
let RJ=null;                // draft id being rejected (asking for the reason)
function dedit(id){ED=id;show()}
function dcancel(){ED=null;show()}
async function dsave(id,btn){
  const ta=document.getElementById("edta-"+id);
  if(!ta)return;
  btn.classList.add("busy");btn.disabled=true;
  try{
    const r=await fetch("/api/draft_action",{method:"POST",
      headers:{"Content-Type":"application/json; charset=utf-8"},
      body:JSON.stringify({id,action:"edit",text:ta.value})});
    const j=await r.json();
    toast(j.message,!j.ok);
    if(j.ok){
      const d=((DR||{}).drafts||[]).find(x=>x.id===id);
      if(d){d.text=j.text;d.final_text=j.final_text;d.edited_at=j.edited_at}
      ED=null;show();
    }
  }catch(e){toast("request failed: "+e,true)}
  btn.classList.remove("busy");btn.disabled=false;
}
// what happened to earlier drafts — read from the git ledger, so it is the
// same story on every machine, including posts released somewhere else
function histHTML(){
  const h=(DR&&DR.history)||[];
  if(!h.length)return"";
  const rows=h.map(x=>{
    const when=x.resolved_at?ago(x.resolved_at):"";
    const live=x.url
      ?`<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.url)}</a>
         <button class="cpy" onclick="cpy('${esc(x.url)}',this)">copy</button>`
      :(x.status==="rejected"
        ?`<span class="why">${esc(x.note&&x.note!=="rejected by operator"
            ?x.note:"no reason given")}</span>`
        :`<span class="why">${esc(x.note||"")}</span>`);
    return `<div class="hrow">
      <span class="badge ${x.status==="approved"?"ok":"pending"}">${esc(x.status)}</span>
      ${platIcon(x.platform,13)}<b>${esc(x.persona)}</b>
      <span class="when">${when}</span>
      <span class="lnk">${live}</span></div>`}).join("");
  return `<h2 style="margin-top:26px">Earlier drafts</h2>
    <div class="meta" style="margin-bottom:10px">Released and rejected drafts from
      the shared ledger — every machine sees the same history, whichever one
      pressed publish.</div>
    <div class="hist">${rows}</div>`;
}
async function cpy(text,btn){
  try{await navigator.clipboard.writeText(text);btn.textContent="copied"}
  catch(e){btn.textContent="press ⌘C"}
  setTimeout(()=>btn.textContent="copy",1600);
}
async function dact(id,action,btn,note){
  if(btn){btn.classList.add("busy");btn.disabled=true}
  try{
    const r=await fetch("/api/draft_action",{method:"POST",
      headers:{"Content-Type":"application/json; charset=utf-8"},
      body:JSON.stringify({id,action,note:note||""})});
    const j=await r.json();
    toast(j.message+(j.url?" → "+j.url:""),!j.ok);
    DR=null;S=null;show();refreshOnce();
  }catch(e){toast("request failed: "+e,true)}
  if(btn){btn.classList.remove("busy");btn.disabled=false}
}
async function refreshOnce(){
  try{S=await (await fetch("/api/state")).json()}catch(e){}
  try{const r=await fetch("/api/drafts");if(r.ok)DR=await r.json()}catch(e){}
  show();
}

// ── screens ─────────────────────────────────────────────────────
const Screens={
approvals:{render(){
  if(!DR)return'<div class="empty">loading the approval queue…</div>';
  let ds=DR.drafts||[];
  const aq=(AQ||"").toLowerCase();
  if(ds.length>5&&aq)ds=ds.filter(d=>`${d.persona} ${d.platform}`.toLowerCase().includes(aq));
  const cards=ds.map(d=>{
    // ledger media when it is on this machine; the provider's URL otherwise
    const src=d.media_local?"/asset?p="+encodeURIComponent(d.media_local)
      :(d.media_remote||"");
    // a carousel is a SET: showing only its cover hides what is being
    // approved, and the operator would have to open files to see slide 3
    const slides=(d.media_slides||[]);
    const med=!src?'<div class="empty">media not on this machine — git pull</div>'
      :slides.length>1
      ?`<div class="slides">${slides.map((p,i)=>
          `<figure><img src="/asset?p=${encodeURIComponent(p)}" alt="${esc(d.alt||"")}">
           <figcaption>${i+1}/${slides.length}</figcaption></figure>`).join("")}</div>`
      :d.media_kind==="video"
      ?`<video src="${src}" controls muted loop></video>`
      :`<img src="${src}" alt="${esc(d.alt||"")}">`;
    return `<div class="appr">
      <div class="med">${med}</div>
      <div>
        <div class="who">${platIcon(d.platform,15)}<b>${esc(d.persona)}</b>
          <span>→ ${esc(d.platform)}</span>
          <span class="badge pending">waiting</span>
          ${d.media_kind==="carousel"
            ?`<span class="badge">carousel · ${(d.media_slides||[]).length} slides</span>`
            :d.media_kind==="video"?`<span class="badge">reel</span>`:""}
          ${d.cover_local?`<a class="badge" href="/asset?p=${encodeURIComponent(d.cover_local)}"
             download target="_blank" title="the frame worth opening on — set it as the cover in the app"
             >cover ↓</a>`:""}
          <span style="margin-left:auto">${ago(d.created_at)}</span></div>
        ${ED===d.id
        ?`<textarea id="edta-${esc(d.id)}" class="edta" spellcheck="false">${esc(d.text||"")}</textarea>
        <div class="altrow">the disclosure line and the platform limit are applied
          automatically on top of this — the preview refreshes when you save</div>
        <div class="acts" style="margin-top:10px">
          <button class="abtn go" onclick="dsave('${esc(d.id)}',this)">✓ Save text</button>
          <button class="abtn" onclick="dcancel()">Cancel</button>
        </div>`
        :`<div class="final"><span class="cap-note">exactly what will be published
          — text, disclosure, hashtags</span>${esc(d.final_text)}</div>
        ${d.alt?`<div class="altrow">alt text: ${esc(d.alt)}</div>`:""}
        ${d.edited_at?`<div class="altrow">✎ text edited by you ${ago(d.edited_at)}</div>`:""}
        ${d.last_error?`<div class="altrow" style="color:var(--redt,#e05e5e)">⚠ last release attempt (${ago(d.last_error_at)}) failed: ${esc(d.last_error)}</div>`:""}
        ${d.note?`<div class="altrow" style="color:var(--ambert)">note: ${esc(d.note)}</div>`:""}
        ${RJ===d.id
        ?`<div class="rjbox">
          <label>Why is this one no good? The next brief for ${esc(d.persona)}
            is written with your answer in hand.</label>
          <input id="rj-${esc(d.id)}" class="rjin" spellcheck="false"
                 placeholder="e.g. placeholder image, not real stock — or: caption reads like an ad"
                 onkeydown="if(event.key==='Enter')document.getElementById('rjgo-${esc(d.id)}').click()">
          <div class="acts" style="margin-top:10px">
            <button id="rjgo-${esc(d.id)}" class="abtn no"
                    onclick="dact('${esc(d.id)}','reject',this,document.getElementById('rj-${esc(d.id)}').value)">✗ Reject</button>
            <button class="abtn" onclick="RJ=null;show()">Cancel</button>
          </div></div>`
        :`<div class="acts" style="margin-top:12px">
          <button class="abtn go" onclick="dact('${esc(d.id)}','approve',this)">✓ Approve &amp; publish</button>
          <button class="abtn" onclick="dedit('${esc(d.id)}')">✎ Edit text</button>
          <button class="abtn no" onclick="RJ='${esc(d.id)}';ED=null;show()">✗ Reject</button>
        </div>`}`}
      </div>
    </div>`}).join("");
  return `<div class="crumb">autoStudio</div>
  ${(DR.drafts||[]).length>5?`<input class="rjin" style="max-width:300px;margin-bottom:10px" placeholder="filter: persona or platform…" value="${esc(AQ)}" onchange="AQ=this.value;show()">`:""}
  <h1>Approvals <span class="clock">${ds.length} waiting ·
    <a onclick="DR=null;show()" style="cursor:pointer">reload</a></span></h1>
  <div class="meta" style="margin-bottom:12px">Each card is a finished post held at the
    door — the cycle did everything except press publish. Approving re-checks the
    safety gates at this moment, then publishes exactly the text shown.</div>
  ${cards||'<div class="empty">nothing waiting — new drafts appear here after each cycle</div>'}
  ${histHTML()}`;
},async load(){
  try{const r=await fetch("/api/drafts");if(r.ok){DR=await r.json();show()}}catch(e){}
}},
overview:{render(){
  if(!S||!F)return'<div class="empty">loading…</div>';
  const A=S.attention||[];
  const crit=A.filter(x=>x.severity==="critical"),
        act=A.filter(x=>x.severity==="action"),
        watch=A.filter(x=>x.severity==="watch");
  const rows=(ACC&&ACC.accounts)||[];
  let posted=0;rows.forEach(a=>posted+=(a.gate&&a.gate.posts_today)||0);
  // the verdict: one sentence a manager reads without knowing the system
  let vcls="ok",vtxt=`<b>All clear.</b><span class="sub">nothing needs you today</span>`;
  if(crit.length)vcls="bad",vtxt=`<b>${crit.length} critical item${crit.length>1?"s":""}.</b>
    <span class="sub">start at the top of the list below</span>`;
  else if(act.length)vcls="warn",vtxt=`<b>${act.length} item${act.length>1?"s":""} need${act.length>1?"":"s"} you.</b>
    <span class="sub">everything else is running itself</span>`;
  // one row per item, the WHOLE row is the button, detail always visible
  const item=x=>`<div class="inb ${x.severity}" onclick="location.hash='${esc(x.screen||"#/overview")}'">
    <b>${esc(x.title)}</b><span class="sub">${esc(x.detail)}</span></div>`;
  const inbox=[...crit,...act].map(item).join("");
  const watchHTML=!watch.length?""
    :WOPEN?watch.map(item).join("")
      +`<div class="inb watch expander" onclick="WOPEN=false;show()">hide the quiet items</div>`
    :`<div class="inb watch expander" onclick="WOPEN=true;show()">
       ${watch.length} quiet item${watch.length>1?"s":""} worth a glance — show</div>`;
  // fleet, aggregated: counts first, then one cell per account (scales to 100s)
  const counts={};rows.forEach(a=>counts[a.status]=(counts[a.status]||0)+1);
  const agg=Object.entries(counts).map(([k,v])=>`${v} ${k}`).join(" · ");
  const cell=a=>`<a class="cell st-${esc(a.status)}${a.gate&&a.gate.open?"":" closed"}"
     href="#/account/${esc(a.platform)}--${esc(a.handle)}"
     title="${esc(a.persona_name||a.persona)} · ${esc(a.platform)} @${esc(a.handle)} — ${esc(a.status)}${a.gate&&!a.gate.open?" · "+esc(a.gate.reason||"gate closed"):""}"></a>`;
  return `<div class="crumb">autoStudio</div>
  <h1>Today <span class="clock">${new Date().toISOString().slice(0,10)}</span></h1>
  <div class="verdict ${vcls}">${vtxt}</div>
  <div class="tiles">
    <a class="tile ${crit.length?"bad":act.length?"warn":""}" href="#/overview"><b>${crit.length+act.length}</b><span>needs you</span></a>
    <a class="tile ${S.drafts_pending?"warn":""}" href="#/approvals"><b>${S.drafts_pending||0}</b><span>drafts waiting for approval</span></a>
    <a class="tile" href="#/accounts"><b>${posted}</b><span>posted today</span></a>
  </div>
  ${inbox||watchHTML?`<h2>Needs you</h2>${inbox||'<div class="empty">nothing — enjoy it</div>'}${watchHTML}`:""}
  <h2>Fleet</h2>
  <div class="meta">${F.personas.length} personas · ${rows.length} account legs${agg?" — "+agg:""}
    · <a href="#/accounts">open the table</a></div>
  <div class="heat">${rows.map(cell).join("")||'<div class="empty">no accounts registered</div>'}</div>`;
},async load(){loadAccounts()}},
accounts:{render(){
  if(!ACC)return'<div class="empty">loading the fleet…</div>';
  const q=(AQ||"").toLowerCase();
  let rows=ACC.accounts.filter(a=>!q
    ||`${a.persona} ${a.persona_name} ${a.platform} ${a.handle} ${a.status}`.toLowerCase().includes(q));
  const keyf={persona:a=>(a.persona_name||a.persona).toLowerCase(),
    status:a=>a.status,followers:a=>-(a.followers||0),
    pending:a=>-(a.pending||0),last:a=>-(Date.parse(a.last_post_at||0)||0)};
  rows=[...rows].sort((x,y)=>{const f=keyf[ASORT]||keyf.persona;
    const a=f(x),b=f(y);return a<b?-1:a>b?1:0});
  const th=(k,label)=>`<th onclick="ASORT='${k}';show()"
    class="${ASORT===k?"on":""}">${label}${ASORT===k?" ↓":""}</th>`;
  const why=a=>{
    const g=a.gate||{};
    if(a.status!=="active")return g.reason||a.status;
    if(!g.open)return g.reason||"gate closed";
    if(!a.credentials_ok&&a.credentials_note)return a.credentials_note;
    return "";};
  const tr=a=>{
    const w=why(a);
    return `<tr onclick="location.hash='#/account/${esc(a.platform)}--${esc(a.handle)}'">
    <td><b>${esc(a.persona_name||a.persona)}</b></td>
    <td>${platIcon(a.platform,13)} @${esc(a.handle)}</td>
    <td><span class="badge ${a.status==="active"&&(!a.gate||a.gate.open)?"ok":a.status==="suspended"?"bad":"pending"}">${esc(a.status)}</span>
        ${w?`<span class="why"> ${esc(w)}</span>`:""}</td>
    <td class="num">${a.followers??"—"}</td>
    <td class="num">${(a.gate&&a.gate.posts_today)||0}/${(a.gate&&a.gate.cap)||"—"}</td>
    <td class="num">${a.pending||""}</td>
    <td>${a.last_post_url
      ?`<a href="${esc(a.last_post_url)}" target="_blank" rel="noopener"
          onclick="event.stopPropagation()">${ago(a.last_post_at)}</a>`
      :'<span class="why">never</span>'}</td></tr>`};
  return `<div class="crumb">autoStudio</div>
  <h1>Accounts <span class="clock">${rows.length} of ${ACC.accounts.length}</span></h1>
  <div class="meta" style="margin-bottom:10px">Every account leg in one table — the
    state's reason is printed on the row, nothing hides behind a click.
    Click a row for that account's full history.</div>
  <input class="rjin" style="max-width:340px;margin-bottom:12px" placeholder="filter: persona, platform, handle, state…"
    value="${esc(AQ)}" onchange="AQ=this.value;show()">
  <div style="overflow-x:auto"><table class="tbl">
    <thead><tr>${th("persona","Persona")}<th>Account</th>${th("status","State")}
      ${th("followers","Followers")}<th>Today</th>${th("pending","Waiting")}${th("last","Last post")}</tr></thead>
    <tbody>${rows.map(tr).join("")}</tbody></table></div>`;
},async load(){loadAccounts()}},
account:{render(arg){
  if(!ACD||ACDleg!==arg)return'<div class="empty">loading the account…</div>';
  const c=ACD.card,g=c.gate||{};
  const series=(ACD.series||[]).map(x=>x.followers??x.subscribers).filter(x=>x!=null);
  let spark="";
  if(series.length>1){
    const mn=Math.min(...series),mx=Math.max(...series),W=560,H=54;
    const pts=series.map((v,i)=>`${(i/(series.length-1)*W).toFixed(1)},${(H-4-(mx>mn?(v-mn)/(mx-mn):0.5)*(H-8)).toFixed(1)}`).join(" ");
    spark=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:${H}px">
      <polyline points="${pts}" fill="none" stroke="var(--teal)" stroke-width="2"/></svg>
      <div class="meta">followers over the metric captures: ${mn} → ${mx}</div>`;
  }
  const hrow=x=>`<div class="hrow">
    <span class="badge ${x.status==="approved"?"ok":"pending"}">${esc(x.status)}</span>
    <span class="when">${x.when?ago(x.when):""}</span>
    <span class="lnk">${String(x.note||"").startsWith("http")
      ?`<a href="${esc(x.note)}" target="_blank" rel="noopener">${esc(x.note)}</a>
        <button class="cpy" onclick="cpy('${esc(x.note)}',this)">copy</button>`
      :`<span class="why">${esc(x.note||"")}</span>`}</span></div>`;
  return `<div class="crumb">autoStudio · <a href="#/accounts">accounts</a></div>
  <h1>${platIcon(c.platform,17)} @${esc(c.handle)}
    <span class="clock">${esc(c.persona_name||c.persona)} · ${esc(c.category||"")}</span></h1>
  <div class="verdict ${c.status==="active"&&g.open?"ok":c.status==="suspended"?"bad":"warn"}">
    <b>${esc(c.status)}${g.open?"":" — gate closed"}.</b>
    <span class="sub">${esc(g.reason||(g.open?"publishing allowed right now":""))}</span></div>
  <div class="facts">
    <div class="fact"><b>${ACD.latest&&(ACD.latest.followers??ACD.latest.subscribers)!=null?(ACD.latest.followers??ACD.latest.subscribers):"—"}</b><span>followers</span></div>
    <div class="fact"><b>${g.posts_today||0}/${g.cap||"—"}</b><span>posted today / cap</span></div>
    <div class="fact"><b>${(ACD.pending||[]).length}</b><span>drafts waiting</span></div>
    <div class="fact"><b>${c.credentials_ok?"yes":"no"}</b><span>keys on this machine</span></div>
    <div class="fact"><b>${esc(c.opened_at||"—")}</b><span>opened</span></div>
  </div>
  ${spark}
  <h2>Post history</h2>
  <div class="meta" style="margin-bottom:8px">From the shared ledger — the same on
    every machine, whichever one pressed publish.</div>
  <div class="hist">${(ACD.history||[]).map(hrow).join("")
    ||'<div class="empty">nothing released or rejected yet</div>'}</div>`;
},async load(arg){
  try{const r=await fetch("/api/account?leg="+encodeURIComponent(arg));
    if(r.ok){ACD=await r.json();ACDleg=arg;show()}}catch(e){}
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
  ${cs.length?pipeBoxHTML(cs[0],false):'<div class="empty">no cycles <b>on this machine</b> — cycle history lives in a local database that git does not carry, so a fresh clone starts empty. Approvals, personas and account health above are live. Run a cycle here with <code>python run.py</code>, or approve the drafts the cloud already made.</div>'}
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
    // reach and saves, not just views: a post shown to 150 people and saved
    // by one is a different problem from a post nobody was shown, and the
    // fixes are opposite. Saves and shares are what the feed acts on next.
    const has=v=>v!==null&&v!==undefined;
    const bits=[];
    if(has(p.views))bits.push(`${p.views} views`);
    if(has(p.reach))bits.push(`${p.reach} reach`);
    if(has(p.likes))bits.push(`${p.likes}♥`);
    if(p.saved)bits.push(`${p.saved} saved`);
    if(p.shares)bits.push(`${p.shares} shared`);
    if(has(p.replies)&&p.replies)bits.push(`${p.replies}💬`);
    const eng=bits.length?bits.join(" · ")
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
    const q=PQ.toLowerCase(),ac=AC();
    let rows=F.personas.filter(p=>{
      const hay=(p.name+p.niche+legsOf(p).map(a=>a.platform+a.handle).join("")).toLowerCase();
      if(q&&!hay.includes(q))return false;
      if(PF==="all")return true;
      if(PF==="attention")return needsAttention(p);
      return legsOf(p).some(a=>a.status===PF)});
    const rank={suspended:0,warming:2,paused:3,active:4};
    rows.sort((a,b)=>(needsAttention(a)?0:1)-(needsAttention(b)?0:1)
      ||rank[worstStatus(a)]-rank[worstStatus(b)]||a.name.localeCompare(b.name));
    document.getElementById("pgrid").innerHTML=
      rows.map(p=>personaTile(p,ac)).join("")||'<div class="empty">no personas match</div>';
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
  const ac=AC();
  const blocks=accts.map(a=>{
    const d=a.diagnosis||{};
    const hc=ac[a.platform+"/"+a.handle];
    const machine=hc?`<div class="meta">this machine:
      <b>${hc.credentials_ok?"key ok":"NO KEY"}</b>${hc.credentials_note
        ?" · "+esc(hc.credentials_note):""} ·
      gate: <b>${esc((hc.gate||{}).open?"open — "+(hc.gate||{}).reason
        :(hc.gate||{}).reason||"—")}</b></div>`:"";
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
        ${machine}
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
// A repaint costs the operator their place on the page: replacing main's
// innerHTML sends the browser back to the top, and on a long approvals queue
// a background refresh every few seconds means you cannot read to the bottom
// of anything. So a repaint has to be worth it — and when it is, it must not
// move the page under the reader.
let PAINT="";
function show(force){
  const {s,arg}=cur();
  const scr=Screens[s]||Screens.overview;
  const html=scr.render(arg);
  const stamp=s+"|"+arg+"|"+html.length+"|"+html;
  if(!force&&stamp===PAINT){
    // same pixels as last time — the only thing that can have changed is
    // the chrome, so leave the reader where they are
    document.getElementById("nav").innerHTML=navHTML();
    document.getElementById("sideinfo").innerHTML=sideHTML();
    return;
  }
  PAINT=stamp;
  const y=window.scrollY;
  document.getElementById("nav").innerHTML=navHTML();
  document.getElementById("sideinfo").innerHTML=sideHTML();
  document.getElementById("main").innerHTML=staleHTML()+html;
  // a real change still keeps the reader's position: they were looking at
  // something, and one draft resolving elsewhere is no reason to move them
  if(y)window.scrollTo(0,y);
  if(scr.after)scr.after(arg);
  if(s==="cycle"&&!CD[arg])Screens.cycle.load(arg);
  if(s==="persona"&&(!PD||PDid!==String(arg)))Screens.persona.load(arg);
  if(s==="signals"&&!PL)Screens.signals.load();
  if(s==="performance"&&!PM)Screens.performance.load();
  if(s==="approvals"&&!DR)Screens.approvals.load();
  if((s==="overview"||s==="accounts")&&!ACC)loadAccounts();
  if(s==="account"&&(!ACD||ACDleg!==arg))Screens.account.load(arg);
}
window.addEventListener("hashchange",()=>{PAINT="";window.scrollTo(0,0);show(true)});
async function refresh(){
  try{S=await (await fetch("/api/state")).json()}catch(e){}
  try{F=await (await fetch("/api/fleet")).json()}catch(e){}
  const {s,arg}=cur();
  // the queue is the app's notification tray — keep it live while it is open
  if(s==="approvals"){try{const r=await fetch("/api/drafts");
    if(r.ok)DR=await r.json()}catch(e){}}
  // don't stomp the personas search box while typing — update grid only
  if(s==="personas"&&document.getElementById("pgrid")){Screens.personas.after();
    document.getElementById("nav").innerHTML=navHTML();
    document.getElementById("sideinfo").innerHTML=sideHTML()}
  else if(s==="cycle"){if(CD[arg]&&CD[arg].status==="running")Screens.cycle.load(arg);
    document.getElementById("nav").innerHTML=navHTML()}
  else if(s==="persona"){document.getElementById("nav").innerHTML=navHTML();
    document.getElementById("sideinfo").innerHTML=sideHTML()}
  // never stomp an open editor: typing a rejection reason or an edited
  // caption must survive the background refresh (same rule as the personas
  // search box above) — update the chrome only, leave main alone
  else if(ED||RJ||["INPUT","TEXTAREA"].includes((document.activeElement||{}).tagName)){
    document.getElementById("nav").innerHTML=navHTML();
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

# What this PROCESS is serving. The page HTML is assembled at import, so a
# `git pull` changes the disk and nothing else: the console keeps serving the
# version it started with, and an operator watching a bug they just fixed has
# no way to tell. Captured here, compared on every state read.
RUNNING_CODE = _version.code_version()

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
                self._send(200, "application/json; charset=utf-8", json.dumps(state()).encode())
            elif parsed.path == "/api/fleet":
                self._send(200, "application/json; charset=utf-8", json.dumps(fleet_state()).encode())
            elif parsed.path == "/api/pool":
                self._send(200, "application/json; charset=utf-8", json.dumps(pool_state()).encode())
            elif parsed.path == "/api/accounts":
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(accounts_state()).encode())
            elif parsed.path == "/api/account":
                leg = parse_qs(parsed.query).get("leg", [""])[0]
                detail = account_detail(leg)
                if detail is None:
                    self._send(404, "application/json; charset=utf-8",
                               b'{"error":"no such account"}')
                else:
                    self._send(200, "application/json; charset=utf-8",
                               json.dumps(detail).encode())
            elif parsed.path == "/api/drafts":
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(drafts_state()).encode())
            elif parsed.path == "/api/performance":
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(performance_state()).encode())
            elif parsed.path == "/api/persona":
                pid = int(parse_qs(parsed.query).get("id", ["0"])[0])
                detail = persona_state(pid)
                if detail is None:
                    self._send(404, "application/json; charset=utf-8", b'{"error":"not found"}')
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps(detail).encode())
            elif parsed.path == "/api/cycle":
                cid = int(parse_qs(parsed.query).get("id", ["0"])[0])
                detail = store.cycle_detail(store.connect(), cid)
                if detail is None:
                    self._send(404, "application/json; charset=utf-8", b'{"error":"not found"}')
                else:
                    self._send(200, "application/json; charset=utf-8", json.dumps(detail).encode())
            elif parsed.path == "/asset":
                p = Path(parse_qs(parsed.query).get("p", [""])[0]).resolve()
                allowed = (ASSETS_DIR.resolve() in p.parents
                           or draftpool.MEDIA_DIR.resolve() in p.parents)
                if allowed and p.exists():
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
        try:
            # re-read .env on every action: on the operator's machine the
            # file IS the source of truth, and an edit (say, pasting a fresh
            # token) must apply on the next click, not the next restart
            try:
                from dotenv import load_dotenv
                load_dotenv(ROOT / ".env", override=True,
                            encoding="utf-8-sig")
            except ImportError:
                pass
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/action":
                result = remediation.apply(store.connect(),
                                           int(body.get("persona_id", 0)),
                                           str(body.get("action", "")),
                                           str(body.get("payload", "")))
            elif parsed.path == "/api/draft_action":
                con = store.connect()
                draft_id = str(body.get("id", ""))
                if body.get("action") == "approve":
                    result = approvals.approve(con, draft_id)
                elif body.get("action") == "reject":
                    result = approvals.reject(con, draft_id,
                                              str(body.get("note", "")))
                elif body.get("action") == "edit":
                    from studio import draftpool
                    from studio.publisher import compose_plain
                    d = draftpool.edit_text(draft_id,
                                            str(body.get("text", "")))
                    final = compose_plain(
                        d.get("text", ""),
                        CAPTION_LIMITS.get(d["platform"], 1000),
                        d.get("provenance"), d.get("persona"))
                    result = {"ok": True, "message": "text saved — the "
                              "preview shows exactly what will publish",
                              "text": d["text"], "final_text": final,
                              "edited_at": d["edited_at"]}
                else:
                    result = {"ok": False, "message": "unknown draft action"}
            else:
                self._send(404, "application/json; charset=utf-8",
                           b'{"ok":false,"message":"not found"}')
                return
            self._send(200, "application/json; charset=utf-8", json.dumps(result).encode())
        except Exception as e:
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"ok": False, "message": str(e)[:200]}).encode())


def _console_already_running(port: int) -> bool:
    """Is the thing holding this port our own console, or a stranger? The
    answer decides whether a busy port is good news or a problem."""
    try:
        import urllib.request
        with urllib.request.urlopen(
                f"http://localhost:{port}/api/state", timeout=2) as r:
            return r.status == 200 and b"attention" in r.read(4096)
    except Exception:
        return False


if __name__ == "__main__":
    # the console is where releases happen, so it must see the operator's
    # platform keys — same .env contract as run.py, shell env still wins
    try:
        from dotenv import load_dotenv
        # utf-8-sig: tolerate the BOM Windows Notepad prepends to UTF-8 files
        found = load_dotenv(ROOT / ".env", encoding="utf-8-sig")
        print(f".env → loaded from {ROOT / '.env'}" if found else
              f".env → not found (looked for {ROOT / '.env'} — "
              "using shell env only)")
    except ImportError:
        print(".env → python-dotenv missing; using shell env only")
    print(_version.banner())

    # A busy port is the most common way this command "fails", and the two
    # causes need opposite responses: the console already running is not an
    # error at all, while an unrelated program squatting on 8377 should not
    # stop the operator from working. Both beat a socket traceback.
    server = None
    for candidate in range(PORT, PORT + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            if candidate == PORT and _console_already_running(PORT):
                url = f"http://localhost:{PORT}"
                print(f"the console is already running → {url}")
                if "--open" in sys.argv:
                    webbrowser.open(url)
                sys.exit(0)
    if server is None:
        print(f"could not bind any port in {PORT}-{PORT + 9} — something is "
              "using them all; close other servers and try again")
        sys.exit(1)

    port = server.server_address[1]
    url = f"http://localhost:{port}"
    if port != PORT:
        print(f"port {PORT} was busy (another program is using it)")
    print(f"ops console → {url}")
    if "--open" in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nconsole stopped")
