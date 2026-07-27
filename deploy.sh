#!/usr/bin/env bash
# Deploy FilamentGuard to the octopi box and restart OctoPrint.
set -euo pipefail

HOST="${1:-root@192.168.1.157}"
DEST=/home/john/octoprint-filamentguard
SRC="$(cd "$(dirname "$0")" && pwd)"

rsync -a --delete \
    --exclude .git --exclude __pycache__ --exclude '*.egg-info' \
    --exclude .pytest_cache \
    "$SRC/" "$HOST:$DEST/"

ssh "$HOST" "
    chown -R john:john $DEST &&
    sudo -u john /home/john/oprint/bin/pip install -q -e $DEST &&
    service octoprint restart
"
echo 'Deployed — OctoPrint restarting.'
