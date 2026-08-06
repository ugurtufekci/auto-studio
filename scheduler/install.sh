#!/bin/bash
# Install / remove / inspect the local launchd schedule (2 cycles a day).
#
#   ./scheduler/install.sh install
#   ./scheduler/install.sh status
#   ./scheduler/install.sh uninstall
#
# Mode is read from .env at run time (SCHEDULER_MODE=dry_run|live), so switching
# between dry runs and live publishing needs no reinstall.
#
# macOS caveat: with the lid closed / machine asleep, launchd cannot fire —
# it runs the missed job once on wake. For 24/7 posting the schedule has to
# live on an always-on host (server or cloud routine).

set -uo pipefail
LABEL="com.autostudio.cycle"
SRC="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$SRC" "$DEST"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
    launchctl bootstrap "gui/$(id -u)" "$DEST"
    echo "installed → 08:00 and 16:00 daily (mode from .env)"
    launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null | grep -E 'state|program' | head -3
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
    rm -f "$DEST"
    echo "removed"
    ;;
  status)
    if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
      echo "loaded:"
      launchctl print "gui/$(id -u)/${LABEL}" | grep -E 'state|runs|last exit' | head -5
    else
      echo "not installed"
    fi
    grep -E '^SCHEDULER_MODE=' .env 2>/dev/null || echo "SCHEDULER_MODE not set (defaults to dry_run)"
    ;;
  *)
    echo "usage: $0 {install|uninstall|status}" ; exit 1 ;;
esac
