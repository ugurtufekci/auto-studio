#!/bin/zsh
# autoStudio — one double-click on macOS: latest code, key check, console.
# Finder runs .command files in Terminal; first launch may need
# right-click → Open to satisfy Gatekeeper.

cd "$(dirname "$0")"

# macOS ships an old python3 (3.9) that this code cannot run, and Homebrew's
# newer one is only on PATH once ~/.zprofile has been set up — which is
# exactly the step a new machine hasn't done yet. So look for a usable
# interpreter rather than trusting the name.
find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 \
                   /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PY=$(find_python)
if [ -z "$PY" ]; then
  echo "No Python 3.11+ found on this Mac."
  echo "The system python3 is $(python3 --version 2>&1), which is too old."
  echo
  echo "Install a newer one:"
  echo "  brew install python@3.12 ffmpeg"
  echo "then run this launcher again."
  echo
  echo "Press any key to close."
  read -k 1
  exit 1
fi
echo "python: $PY ($($PY --version 2>&1))"

echo
echo "=== updating from origin/main ==="
git pull origin main || echo "could not pull — continuing with the code already here"

echo
echo "=== checking the Instagram keys ==="
"$PY" -m studio.publisher_instagram

echo
echo "=== opening the console ==="
(sleep 1 && open http://localhost:8377) &
"$PY" dashboard/serve.py

echo
echo "The console stopped. You can close this window."
