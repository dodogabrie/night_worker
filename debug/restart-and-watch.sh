#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    LOG_DIR=$(grep '^LOG_DIR=' "$SCRIPT_DIR/.env" | cut -d= -f2)
fi
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"

sudo systemctl restart night-worker
echo "Service restarted. Watching logs..."
echo "Press Ctrl+C to stop watching."
echo
tail -f "$LOG_DIR/night-worker.log"
