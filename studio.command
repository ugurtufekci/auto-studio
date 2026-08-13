#!/bin/zsh
# autoStudio — one double-click on macOS: latest code, key check, console.
# Finder runs .command files in Terminal; first launch may need
# right-click → Open to satisfy Gatekeeper.

cd "$(dirname "$0")"

echo "=== updating from origin/main ==="
git pull origin main || echo "could not pull — continuing with the code already here"

echo
echo "=== checking the Instagram keys ==="
python3 -m studio.publisher_instagram

echo
echo "=== opening the console ==="
(sleep 1 && open http://localhost:8377) &
python3 dashboard/serve.py

echo
echo "The console stopped. You can close this window."
