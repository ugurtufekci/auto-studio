#!/bin/bash
# Wrapper launchd calls twice a day. Reads SCHEDULER_MODE from .env so you can
# flip dry-run ↔ live without touching the launchd job.
#
#   SCHEDULER_MODE=dry_run  (default) — full pipeline, nothing published
#   SCHEDULER_MODE=live              — publishes (guardrails still apply)

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

MODE="dry_run"
if [ -f .env ]; then
  MODE=$(grep -E '^SCHEDULER_MODE=' .env | tail -1 | cut -d= -f2- | tr -d ' "'"'"'')
  MODE=${MODE:-dry_run}
fi

ARGS=()
[ "$MODE" != "live" ] && ARGS+=("--dry-run")

LOG="store/scheduler.log"
mkdir -p store
{
  echo "───────── $(date '+%Y-%m-%d %H:%M:%S') · mode=$MODE ─────────"
  .venv/bin/python run.py "${ARGS[@]}"
  echo "exit=$?"
} >> "$LOG" 2>&1

# keep the log from growing forever (last ~2000 lines)
if [ "$(wc -l < "$LOG")" -gt 3000 ]; then
  tail -2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
