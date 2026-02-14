#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    LOG_DIR=$(grep '^LOG_DIR=' "$SCRIPT_DIR/.env" | cut -d= -f2)
fi
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"

echo "=== Triggering Night Worker Loop ==="
echo

# Clear any failed/claimed state
echo "1. Clearing old job state..."
rm -f "$SCRIPT_DIR/.state/failed/"* "$SCRIPT_DIR/.state/queue/"*.claimed 2>/dev/null
echo "   Done"
echo

# Touch the trigger file
TRIGGER_FILE="/mnt/nextcloud-data/edoardo/files/RalphLoop/in/start.md"
echo "2. Touching trigger file: $TRIGGER_FILE"
sudo touch "$TRIGGER_FILE"
sudo chown www-data:www-data "$TRIGGER_FILE"
echo "   Trigger file updated!"
echo

echo "3. Watching logs (Ctrl+C to stop)..."
tail -f "$LOG_DIR/night-worker.log"
