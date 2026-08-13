#!/bin/zsh
# autoStudio — one double-click on macOS: environment, latest code, console.
# Finder runs .command files in Terminal; first launch may need
# right-click → Open to satisfy Gatekeeper.

cd "$(dirname "$0")"

# macOS ships an old python3 (3.9) that this code cannot run, and Homebrew
# deliberately does NOT put its newer one under that name — so look for an
# interpreter by version rather than trusting the name.
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

# A private virtualenv, not the system interpreter: Homebrew's Python refuses
# `pip install` into itself (PEP 668), and a shared install is what makes a
# machine's Python quietly diverge from what the studio was tested against.
# Rebuilt whenever requirements.txt is newer than the last successful install.
VENV=".venv"
STAMP="$VENV/.requirements-installed"
if [ ! -d "$VENV" ]; then
  echo
  echo "=== first run: building the environment (a minute or two) ==="
  "$PY" -m venv "$VENV" || { echo "could not create $VENV"; read -k 1; exit 1; }
fi
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo
  echo "=== installing dependencies ==="
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  if "$VENV/bin/python" -m pip install -r requirements.txt; then
    touch "$STAMP"
  else
    echo "dependency install failed — see the error above"
    read -k 1
    exit 1
  fi
fi
PY="$VENV/bin/python"

if [ ! -f .env ]; then
  echo
  echo "No .env yet — copying .env.example, which describes what to paste."
  cp .env.example .env
  open -e .env
fi

echo
echo "=== checking the Instagram keys ==="
"$PY" -m studio.publisher_instagram

echo
echo "=== opening the console ==="
# the server picks the port and opens the browser on the one it actually got;
# a hardcoded URL here would open the wrong page whenever 8377 is busy
"$PY" dashboard/serve.py --open

echo
echo "The console stopped. You can close this window."
