"""Account remediation — diagnose a persona's problem, offer the right fix.

The ops loop the dashboard drives:
    symptom  →  diagnosis  →  remedy (auto or human)  →  audit trail

Auto remedies run here. Human-only remedies (appeals, billing, credentials)
are returned as instructions with a tracked state, never silently skipped.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT / "store" / "bsky_session.txt"

# action -> (label, kind)  kind: auto | human | run
CATALOG = {
    "recheck":       ("Re-check platform status", "auto"),
    "pause":         ("Pause persona", "auto"),
    "resume":        ("Resume persona", "auto"),
    "clear_session": ("Clear saved session (force fresh login)", "auto"),
    "reset_warmup":  ("Reset warm-up clock (after reinstatement)", "auto"),
    "run_cycle":     ("Run a cycle now (dry run)", "run"),
    "run_cycle_live": ("Run a cycle now (publish)", "run"),
    "mark_appeal":   ("Mark appeal as submitted", "auto"),
    "note":          ("Add operator note", "auto"),
}


def diagnose_account(con, persona: dict, a: dict) -> dict:
    """Diagnose ONE platform leg. A persona can be healthy on Telegram and
    suspended on Bluesky — the diagnosis belongs to the account, not the name."""
    merged = {**persona, **{k: v for k, v in a.items() if v is not None},
              "demo": persona["demo"]}
    d = diagnose(con, merged)
    d["platform"] = a["platform"]
    d["handle"] = a.get("handle", "")
    return d


def diagnose(con, p: dict) -> dict:
    """Return {severity, symptom, cause, remedies:[action…], human_steps:[…]}."""
    from studio import guard

    if p["status"] == "suspended":
        return {
            "severity": "critical",
            "symptom": "Account suspended by the platform",
            "cause": "Platform moderation takedown — automated posting stopped. "
                     "Most common trigger: bot-like behaviour pattern on a young "
                     "account (burst logins, search sweeps, identity rewrite).",
            "remedies": ["recheck", "mark_appeal", "reset_warmup", "note"],
            "human_steps": [
                "Appeal from the platform's suspension screen or moderation email",
                "Do NOT create a replacement account before the appeal resolves "
                "(ban-evasion pattern)",
                "After reinstatement: reset the warm-up clock so automation "
                "stays silent 48h, then 1 post/day",
            ],
        }

    if p["status"] == "paused":
        return {"severity": "info", "symptom": "Persona paused by operator",
                "cause": "Automation is intentionally halted for this persona.",
                "remedies": ["resume", "note"], "human_steps": []}

    note = (p.get("last_cycle_note") or "").lower()
    if p.get("last_cycle_status") == "failed":
        if "balance" in note or "credit" in note or "locked" in note:
            return {
                "severity": "high",
                "symptom": "Last cycle failed — media provider out of credit",
                "cause": p.get("last_cycle_note") or "provider balance exhausted",
                "remedies": ["run_cycle", "note"],
                "human_steps": ["Top up the provider balance, then re-run the cycle"],
            }
        if "auth" in note or "login" in note or "token" in note or "401" in note:
            return {
                "severity": "high",
                "symptom": "Last cycle failed — authentication problem",
                "cause": p.get("last_cycle_note") or "session/credential rejected",
                "remedies": ["clear_session", "run_cycle", "note"],
                "human_steps": ["If clearing the session doesn't help, rotate the "
                                "app password on the platform and update .env"],
            }
        if "guardrail" in note:
            return {
                "severity": "info",
                "symptom": "Cycle skipped by its own guardrail",
                "cause": p.get("last_cycle_note") or "cadence/warm-up policy",
                "remedies": ["note"],
                "human_steps": ["This is the safety policy working — adjust "
                                "config/platform_policy.yaml only if intended"],
            }
        return {
            "severity": "high",
            "symptom": "Last cycle failed",
            "cause": p.get("last_cycle_note") or "see the cycle's event timeline",
            "remedies": ["run_cycle", "recheck", "note"],
            "human_steps": ["Open the cycle detail and read the failing stage"],
        }

    silent = False
    if p.get("last_post_at"):
        age_h = (datetime.now(UTC)
                 - datetime.fromisoformat(p["last_post_at"])).total_seconds() / 3600
        silent = age_h > 36
    if p["status"] == "active" and silent:
        return {
            "severity": "medium",
            "symptom": "Active but silent for over 36h",
            "cause": "Scheduler may not be firing, or every cycle is being "
                     "blocked before publish.",
            "remedies": ["run_cycle", "recheck", "note"],
            "human_steps": ["Check the scheduler is installed and the last "
                            "cycles' event timelines"],
        }

    if not p["demo"]:
        ok, reason = guard.can_post(con, p.get("platform", "bluesky"))
        if not ok:
            return {"severity": "info", "symptom": "Publishing paused by policy",
                    "cause": reason, "remedies": ["note"],
                    "human_steps": ["Nothing to do — the warm-up/cadence policy "
                                    "is protecting the account"]}

    return {"severity": "ok", "symptom": "Healthy", "cause": "",
            "remedies": ["pause", "run_cycle", "note"], "human_steps": []}


# ── auto remedies ───────────────────────────────────────────────

def apply(con, persona_id: int, action: str, payload: str = "") -> dict:
    """Execute an action on one persona, scoped to a platform where that
    matters (payload carries the platform for account-level actions).
    Returns {ok, message}. Everything is audit-logged."""
    from studio import guard, store

    p = store.get_persona(con, persona_id)
    if not p:
        return {"ok": False, "message": "persona not found"}
    if action not in CATALOG:
        return {"ok": False, "message": f"unknown action '{action}'"}

    account_scoped = {"pause", "resume", "recheck"}
    platform = payload.strip() if action in account_scoped else ""
    if platform:
        p = {**p, "platform": platform}

    demo = bool(p["demo"])
    platform_actions = {"recheck", "clear_session", "run_cycle", "run_cycle_live"}
    if demo and action in platform_actions:
        return {"ok": False, "message": "demo persona — no real account behind it; "
                                       "status changes and notes work for the walkthrough"}

    if action in ("pause", "resume"):
        new = "paused" if action == "pause" else "active"
        n = store.set_account_status(con, persona_id, platform, new)
        if n:
            msg = (f"{platform or 'all platforms'} {new}"
                   + (" — no cycles will publish there" if new == "paused" else ""))
        else:
            store.set_persona_status(con, persona_id, new)
            msg = f"persona {new}"
    elif action == "note":
        msg = payload.strip()[:400] or "(empty note)"
    elif action == "mark_appeal":
        msg = "appeal submitted — awaiting platform response"
    elif action == "clear_session":
        existed = SESSION_FILE.exists()
        SESSION_FILE.unlink(missing_ok=True)
        msg = ("saved session cleared — next cycle performs a fresh login"
               if existed else "no saved session was present")
    elif action == "reset_warmup":
        guard.reset_warmup(con)
        msg = ("warm-up clock reset — automation silent 48h, then 1 post/day, "
               "then 2/day")
    elif action == "recheck":
        msg = _recheck(con, persona_id, p)
    elif action in ("run_cycle", "run_cycle_live"):
        msg = _spawn_cycle(live=(action == "run_cycle_live"))
    else:
        return {"ok": False, "message": "unhandled action"}

    store.log_action(con, persona_id, action, msg)
    return {"ok": True, "message": msg}


def _recheck(con, persona_id: int, p: dict) -> str:
    """Re-probe one platform and sync that account's stored status."""
    from studio import store

    platform = p.get("platform", "")
    handle = p.get("handle", "")

    def sync(status: str):
        if not store.set_account_status(con, persona_id, platform, status):
            store.set_persona_status(con, persona_id, status)

    if platform == "bluesky":
        try:
            r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                          params={"actor": handle}, timeout=10)
            if r.status_code == 200:
                sync("active")
                d = r.json()
                return (f"bluesky says OK — reinstated. {d.get('followersCount', 0)} "
                        f"followers, {d.get('postsCount', 0)} posts. "
                        f"Run 'reset warm-up' before resuming automation.")
            if "AccountTakedown" in r.text:
                sync("suspended")
                return "bluesky still reports the account as suspended"
            return f"unexpected bluesky response: HTTP {r.status_code}"
        except Exception as e:
            return f"probe failed: {str(e)[:120]}"

    if platform == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat = os.environ.get("TELEGRAM_CHANNEL", handle)
        if not token:
            return "TELEGRAM_BOT_TOKEN not set — cannot probe"
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getChatMember",
                          params={"chat_id": chat, "user_id": token.split(":")[0]},
                          timeout=10).json()
            if r.get("ok"):
                m = r["result"]
                if m.get("status") == "administrator" and m.get("can_post_messages"):
                    sync("active")
                    return f"telegram OK — bot is admin of {chat} with post rights"
                sync("paused")
                return (f"telegram: bot status is '{m.get('status')}', "
                        f"can_post_messages={m.get('can_post_messages')} — "
                        f"re-add it as channel admin with posting rights")
            return f"telegram probe failed: {str(r.get('description'))[:120]}"
        except Exception as e:
            return f"probe failed: {str(e)[:120]}"

    return f"no status probe implemented for {platform or 'this platform'}"


def _spawn_cycle(live: bool) -> str:
    """Fire run.py detached so the dashboard stays responsive."""
    cmd = [sys.executable, str(ROOT / "run.py"), "--now"]
    if not live:
        cmd.append("--dry-run")
    log_path = ROOT / "store" / "last_manual_run.log"
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "w") as log:
        subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"},
                         start_new_session=True)
    return (f"cycle started ({'publish' if live else 'dry run'}) — watch the "
            f"Pipeline screen; log: store/last_manual_run.log")
