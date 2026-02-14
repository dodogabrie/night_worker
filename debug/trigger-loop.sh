#!/bin/bash

echo "=== Triggering Night Worker Loop ==="
echo

# Clear any failed/claimed state
echo "1. Clearing old job state..."
rm -f .state/failed/* .state/queue/*.claimed 2>/dev/null
echo "   ✓ State cleared"
echo

# Touch the trigger file
TRIGGER_FILE="/mnt/nextcloud-data/edoardo/files/RalphLoop/in/start.md"
echo "2. Touching trigger file: $TRIGGER_FILE"
sudo touch "$TRIGGER_FILE"
sudo chown www-data:www-data "$TRIGGER_FILE"
echo "   ✓ Trigger file updated!"
echo

echo "3. Watching logs (Ctrl+C to stop)..."
tail -f /var/log/night-worker.log
