"""Lineage store — handbook page 19 (Data model), miniature.

The S0 exit condition: any live post traces back to the brief, the signal
and the assets that produced it. One SQLite file, append-only in spirit.

    cycles → signals → briefs → assets
                          └──→ posts
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "store" / "studio.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    raw_item_count INTEGER,
    signal_count INTEGER,
    status TEXT DEFAULT 'running',          -- running | published | dry_run | failed
    note TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER REFERENCES cycles(id),
    topic TEXT, signal_type TEXT, summary TEXT, why_now TEXT,
    velocity REAL, niche_fit REAL, producibility REAL,
    expiry_hours INTEGER, score REAL, source_count INTEGER,
    exemplar_urls TEXT,                      -- json list
    chosen INTEGER DEFAULT 0,                -- 1 = this cycle posted about it
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    signal_id INTEGER REFERENCES signals(id),
    format TEXT,                             -- image_post | slideshow_video | hero_clip
    premise TEXT, angle TEXT, mood TEXT,
    caption TEXT, alt_text TEXT,
    voiceover_script TEXT,
    image_prompts TEXT,                      -- json list
    model TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    brief_id INTEGER REFERENCES briefs(id),
    kind TEXT,                               -- image | audio | video
    path TEXT, model TEXT, prompt TEXT,
    chosen INTEGER DEFAULT 0,
    meta TEXT,                               -- json: judge notes, timings, cost
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS renditions (
    -- one brief → many platform-native cuts. YouTube carries title/tags
    -- instead of a caption, hence the generic columns.
    id INTEGER PRIMARY KEY,
    brief_id INTEGER REFERENCES briefs(id),
    platform TEXT,
    text TEXT,
    title TEXT,
    tags TEXT,                               -- json list
    model TEXT,
    created_at TEXT,
    UNIQUE(brief_id, platform)
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    brief_id INTEGER REFERENCES briefs(id),
    platform TEXT, uri TEXT, url TEXT,
    text TEXT,
    status TEXT,                             -- published | dry_run | failed
    posted_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER REFERENCES cycles(id),
    stage TEXT,                              -- collect|signals|brief|render|voiceover|assemble|publish|error
    status TEXT,                             -- running | progress | done | failed
    detail TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    -- one row per persona × platform. The persona is the identity; accounts are
    -- its legs on each network, each with its own handle, status and cadence.
    id INTEGER PRIMARY KEY,
    persona_id INTEGER REFERENCES personas(id),
    platform TEXT,
    handle TEXT,
    status TEXT DEFAULT 'active',            -- active | warming | paused | suspended
    cadence TEXT,
    demo INTEGER DEFAULT 0,
    posts_today INTEGER DEFAULT 0,           -- stored for demo rows only
    last_post_at TEXT,
    last_cycle_status TEXT,
    created_at TEXT,
    UNIQUE(persona_id, platform)
);
CREATE TABLE IF NOT EXISTS persona_actions (
    id INTEGER PRIMARY KEY,
    persona_id INTEGER REFERENCES personas(id),
    action TEXT,                             -- pause|resume|reset_warmup|recheck|…
    detail TEXT,
    actor TEXT DEFAULT 'operator',           -- who did it (audit trail)
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY,
    name TEXT, handle TEXT, platform TEXT, niche TEXT,
    status TEXT DEFAULT 'active',            -- active | warming | paused | suspended
    cadence TEXT,
    demo INTEGER DEFAULT 0,                  -- 1 = synthetic fleet-demo row
    posts_today INTEGER DEFAULT 0,           -- stored for demo rows; computed live for real ones
    last_post_at TEXT,
    last_cycle_status TEXT,
    created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# ── writes ──────────────────────────────────────────────────────

def start_cycle(con, raw_item_count: int) -> int:
    cur = con.execute(
        "INSERT INTO cycles (started_at, raw_item_count) VALUES (?, ?)",
        (_now(), raw_item_count))
    con.commit()
    return cur.lastrowid


def finish_cycle(con, cycle_id: int, status: str, note: str = ""):
    con.execute(
        "UPDATE cycles SET finished_at=?, status=?, note=?, "
        "signal_count=(SELECT COUNT(*) FROM signals WHERE cycle_id=?) WHERE id=?",
        (_now(), status, note, cycle_id, cycle_id))
    con.commit()


def save_signals(con, cycle_id: int, signals: list[dict]) -> list[int]:
    ids = []
    for s in signals:
        cur = con.execute(
            "INSERT INTO signals (cycle_id, topic, signal_type, summary, why_now,"
            " velocity, niche_fit, producibility, expiry_hours, score, source_count,"
            " exemplar_urls, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cycle_id, s["topic"], s["signal_type"], s["summary"], s["why_now"],
             s["velocity"], s["niche_fit"], s["producibility"], s["expiry_hours"],
             s["score"], s.get("source_count", 1),
             json.dumps(s.get("exemplar_urls", [])), _now()))
        ids.append(cur.lastrowid)
    con.commit()
    return ids


def mark_chosen_signal(con, signal_id: int):
    con.execute("UPDATE signals SET chosen=1 WHERE id=?", (signal_id,))
    con.commit()


def save_brief(con, signal_id: int, brief: dict) -> int:
    cur = con.execute(
        "INSERT INTO briefs (signal_id, format, premise, angle, mood, caption,"
        " alt_text, voiceover_script, image_prompts, model, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, brief["format"], brief["premise"], brief["angle"], brief["mood"],
         brief["caption"], brief.get("alt_text", ""), brief.get("voiceover_script", ""),
         json.dumps(brief.get("image_prompts", [])), brief.get("model", ""), _now()))
    con.commit()
    return cur.lastrowid


def save_asset(con, brief_id: int, kind: str, path: str, model: str,
               prompt: str = "", chosen: bool = False, meta: dict | None = None) -> int:
    cur = con.execute(
        "INSERT INTO assets (brief_id, kind, path, model, prompt, chosen, meta, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (brief_id, kind, path, model, prompt, int(chosen),
         json.dumps(meta or {}), _now()))
    con.commit()
    return cur.lastrowid


def mark_chosen_asset(con, asset_id: int):
    con.execute("UPDATE assets SET chosen=1 WHERE id=?", (asset_id,))
    con.commit()


def save_renditions(con, brief_id: int, rends: dict, model: str = "") -> None:
    for platform, r in rends.items():
        con.execute(
            "INSERT OR REPLACE INTO renditions (brief_id, platform, text, title,"
            " tags, model, created_at) VALUES (?,?,?,?,?,?,?)",
            (brief_id, platform,
             r.get("text") or r.get("description", ""),
             r.get("title", ""),
             json.dumps(r.get("tags", [])), model, _now()))
    con.commit()


def renditions_for(con, brief_id: int) -> list[dict]:
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM renditions WHERE brief_id=? ORDER BY platform",
        (brief_id,)).fetchall()]
    for r in rows:
        r["tags"] = json.loads(r["tags"] or "[]")
    return rows


def save_post(con, brief_id: int, platform: str, uri: str, url: str,
              text: str, status: str) -> int:
    cur = con.execute(
        "INSERT INTO posts (brief_id, platform, uri, url, text, status, posted_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (brief_id, platform, uri, url, text, status, _now()))
    con.commit()
    return cur.lastrowid


def log_event(con, cycle_id: int, stage: str, status: str, detail: str = ""):
    con.execute(
        "INSERT INTO events (cycle_id, stage, status, detail, created_at) VALUES (?,?,?,?,?)",
        (cycle_id, stage, status, detail, _now()))
    con.commit()


def update_cycle_raw(con, cycle_id: int, raw_item_count: int):
    con.execute("UPDATE cycles SET raw_item_count=? WHERE id=?",
                (raw_item_count, cycle_id))
    con.commit()


# ── reads (dashboard: "why does this post exist") ───────────────

def lineage(con) -> list[dict]:
    """Every post with its full chain, newest first."""
    rows = con.execute("""
        SELECT p.id post_id, p.platform, p.url post_url, p.text post_text,
               p.status, p.posted_at,
               b.id brief_id, b.format, b.premise, b.angle, b.mood,
               b.image_prompts, b.voiceover_script,
               s.id signal_id, s.topic, s.signal_type, s.summary, s.why_now,
               s.velocity, s.niche_fit, s.producibility, s.score, s.source_count,
               s.exemplar_urls,
               c.id cycle_id, c.started_at cycle_started, c.raw_item_count
        FROM posts p
        JOIN briefs b ON p.brief_id = b.id
        JOIN signals s ON b.signal_id = s.id
        JOIN cycles c ON s.cycle_id = c.id
        ORDER BY p.id DESC
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["assets"] = [dict(a) for a in con.execute(
            "SELECT * FROM assets WHERE brief_id=? ORDER BY chosen DESC, id",
            (r["brief_id"],)).fetchall()]
        out.append(d)
    return out


def cycles_with_events(con, limit: int = 10) -> list[dict]:
    cycles = [dict(r) for r in con.execute(
        "SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    for c in cycles:
        c["events"] = [dict(e) for e in con.execute(
            "SELECT stage, status, detail, created_at FROM events "
            "WHERE cycle_id=? ORDER BY id", (c["id"],)).fetchall()]
    return cycles


def recent_assets(con, limit: int = 24) -> list[dict]:
    return [dict(r) for r in con.execute("""
        SELECT a.id, a.kind, a.path, a.model, a.chosen, a.created_at,
               s.topic
        FROM assets a
        JOIN briefs b ON a.brief_id = b.id
        JOIN signals s ON b.signal_id = s.id
        ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()]


def ensure_persona(con, name: str, handle: str, platform: str, niche: str,
                   cadence: str) -> int:
    """Upsert the real persona as row #1 (Mara). Demo rows live above id 1."""
    row = con.execute("SELECT id FROM personas WHERE demo=0 AND handle=?",
                      (handle,)).fetchone()
    if row:
        con.execute("UPDATE personas SET name=?, platform=?, niche=?, cadence=? WHERE id=?",
                    (name, platform, niche, cadence, row["id"]))
        con.commit()
        return row["id"]
    cur = con.execute(
        "INSERT INTO personas (name, handle, platform, niche, cadence, demo, created_at)"
        " VALUES (?,?,?,?,?,0,?)", (name, handle, platform, niche, cadence, _now()))
    con.commit()
    return cur.lastrowid


def ensure_account(con, persona_id: int, platform: str, handle: str,
                   cadence: str = "") -> int:
    row = con.execute("SELECT id FROM accounts WHERE persona_id=? AND platform=?",
                      (persona_id, platform)).fetchone()
    if row:
        con.execute("UPDATE accounts SET handle=?, cadence=? WHERE id=?",
                    (handle, cadence, row["id"]))
        con.commit()
        return row["id"]
    cur = con.execute(
        "INSERT INTO accounts (persona_id, platform, handle, cadence, demo, created_at)"
        " VALUES (?,?,?,?,0,?)", (persona_id, platform, handle, cadence, _now()))
    con.commit()
    return cur.lastrowid


def _live_account_stats(con, platform: str) -> dict:
    """Per-platform stats computed from the real posts table."""
    posts_today = con.execute(
        "SELECT COUNT(*) FROM posts WHERE status='published' AND platform=? "
        "AND date(posted_at)=date('now')", (platform,)).fetchone()[0]
    last = con.execute(
        "SELECT posted_at FROM posts WHERE status='published' AND platform=? "
        "ORDER BY id DESC LIMIT 1", (platform,)).fetchone()
    cyc = con.execute(
        "SELECT c.status FROM cycles c JOIN signals s ON s.cycle_id=c.id "
        "JOIN briefs b ON b.signal_id=s.id JOIN posts p ON p.brief_id=b.id "
        "WHERE p.platform=? ORDER BY c.id DESC LIMIT 1", (platform,)).fetchone()
    return {"posts_today": posts_today,
            "last_post_at": last["posted_at"] if last else None,
            "last_cycle_status": cyc["status"] if cyc else None}


def fleet(con) -> list[dict]:
    """Personas, each carrying its per-platform accounts. Real accounts get
    live-computed stats; demo rows use their stored values."""
    personas = [dict(r) for r in con.execute(
        "SELECT * FROM personas ORDER BY demo, id").fetchall()]
    for p in personas:
        accounts = [dict(a) for a in con.execute(
            "SELECT * FROM accounts WHERE persona_id=? ORDER BY platform",
            (p["id"],)).fetchall()]
        if not accounts:
            # personas seeded before the accounts table existed: synthesise one
            # account row from the persona's own platform/handle columns
            accounts = [{"id": None, "persona_id": p["id"], "platform": p["platform"],
                         "handle": p["handle"], "status": p["status"],
                         "cadence": p["cadence"], "demo": p["demo"],
                         "posts_today": p["posts_today"],
                         "last_post_at": p["last_post_at"],
                         "last_cycle_status": p["last_cycle_status"]}]
        for a in accounts:
            if not a["demo"]:
                a.update(_live_account_stats(con, a["platform"]))
        p["accounts"] = accounts
        p["posts_today"] = sum(a.get("posts_today") or 0 for a in accounts)
        p["platforms"] = [a["platform"] for a in accounts]
    return personas


# ── persona remediation (dashboard-driven account management) ────

def log_action(con, persona_id: int, action: str, detail: str = "",
               actor: str = "operator"):
    con.execute("INSERT INTO persona_actions (persona_id, action, detail, actor,"
                " created_at) VALUES (?,?,?,?,?)",
                (persona_id, action, detail, actor, _now()))
    con.commit()


def set_persona_status(con, persona_id: int, status: str):
    con.execute("UPDATE personas SET status=? WHERE id=?", (status, persona_id))
    con.commit()


def set_account_status(con, persona_id: int, platform: str, status: str) -> int:
    """Status lives on the account, not the persona. Returns rows changed
    (0 means this persona has no accounts row yet — caller falls back)."""
    if platform:
        cur = con.execute("UPDATE accounts SET status=? WHERE persona_id=? AND platform=?",
                          (status, persona_id, platform))
    else:
        cur = con.execute("UPDATE accounts SET status=? WHERE persona_id=?",
                          (status, persona_id))
    con.commit()
    return cur.rowcount


def get_persona(con, persona_id: int) -> dict | None:
    row = con.execute("SELECT * FROM personas WHERE id=?", (persona_id,)).fetchone()
    return dict(row) if row else None


def persona_detail(con, persona_id: int) -> dict | None:
    p = get_persona(con, persona_id)
    if not p:
        return None
    accounts = [dict(a) for a in con.execute(
        "SELECT * FROM accounts WHERE persona_id=? ORDER BY platform",
        (persona_id,)).fetchall()]
    if not accounts:
        accounts = [{"id": None, "persona_id": persona_id, "platform": p["platform"],
                     "handle": p["handle"], "status": p["status"],
                     "cadence": p["cadence"], "demo": p["demo"],
                     "posts_today": p["posts_today"],
                     "last_post_at": p["last_post_at"],
                     "last_cycle_status": p["last_cycle_status"]}]
    for a in accounts:
        if not a["demo"]:
            a.update(_live_account_stats(con, a["platform"]))
            a["recent_posts"] = [dict(r) for r in con.execute(
                "SELECT platform, status, url, posted_at FROM posts WHERE platform=? "
                "ORDER BY id DESC LIMIT 5", (a["platform"],)).fetchall()]
        else:
            a["recent_posts"] = []
    p["accounts"] = accounts
    if not p["demo"]:
        p["posts_today"] = con.execute(
            "SELECT COUNT(*) FROM posts WHERE status='published' "
            "AND date(posted_at)=date('now')").fetchone()[0]
        last = con.execute("SELECT posted_at FROM posts WHERE status='published' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        p["last_post_at"] = last["posted_at"] if last else None
        lc = con.execute("SELECT id, status, note FROM cycles ORDER BY id DESC "
                         "LIMIT 1").fetchone()
        p["last_cycle_status"] = lc["status"] if lc else None
        p["last_cycle_id"] = lc["id"] if lc else None
        p["last_cycle_note"] = lc["note"] if lc else ""
        p["recent_posts"] = [dict(r) for r in con.execute(
            "SELECT platform, status, url, posted_at FROM posts "
            "ORDER BY id DESC LIMIT 8").fetchall()]
    else:
        p["recent_posts"] = []
        p["last_cycle_id"] = None
        p["last_cycle_note"] = ""
    p["actions"] = [dict(r) for r in con.execute(
        "SELECT action, detail, actor, created_at FROM persona_actions "
        "WHERE persona_id=? ORDER BY id DESC LIMIT 25", (persona_id,)).fetchall()]
    return p


def cycle_detail(con, cycle_id: int) -> dict | None:
    """Everything one cycle did — the dashboard's drill-down view."""
    c = con.execute("SELECT * FROM cycles WHERE id=?", (cycle_id,)).fetchone()
    if not c:
        return None
    out = dict(c)
    out["events"] = [dict(e) for e in con.execute(
        "SELECT stage, status, detail, created_at FROM events "
        "WHERE cycle_id=? ORDER BY id", (cycle_id,)).fetchall()]
    sigs = [dict(r) for r in con.execute(
        "SELECT * FROM signals WHERE cycle_id=? ORDER BY score DESC",
        (cycle_id,)).fetchall()]
    for s in sigs:
        s["exemplar_urls"] = json.loads(s["exemplar_urls"] or "[]")
    out["signals"] = sigs
    briefs = [dict(r) for r in con.execute(
        "SELECT b.* FROM briefs b JOIN signals s ON b.signal_id=s.id "
        "WHERE s.cycle_id=? ORDER BY b.id", (cycle_id,)).fetchall()]
    for b in briefs:
        b["image_prompts"] = json.loads(b["image_prompts"] or "[]")
        b["assets"] = [dict(a) for a in con.execute(
            "SELECT * FROM assets WHERE brief_id=? ORDER BY chosen DESC, id",
            (b["id"],)).fetchall()]
        for a in b["assets"]:
            a["meta"] = json.loads(a["meta"] or "{}")
        b["posts"] = [dict(p) for p in con.execute(
            "SELECT * FROM posts WHERE brief_id=?", (b["id"],)).fetchall()]
    out["briefs"] = briefs
    return out


def stats(con) -> dict:
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "cycles": q("SELECT COUNT(*) FROM cycles"),
        "raw_items": q("SELECT COALESCE(SUM(raw_item_count),0) FROM cycles"),
        "signals": q("SELECT COUNT(*) FROM signals"),
        "assets": q("SELECT COUNT(*) FROM assets"),
        "posts_published": q("SELECT COUNT(*) FROM posts WHERE status='published'"),
        "posts_dry": q("SELECT COUNT(*) FROM posts WHERE status='dry_run'"),
    }


if __name__ == "__main__":
    con = connect()
    print(f"db ready at {DB_PATH}")
    for t in ("cycles", "signals", "briefs", "assets", "posts", "events"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n} rows")
