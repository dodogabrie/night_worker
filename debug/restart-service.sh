#!/bin/bash
set -e

echo "=== Night Worker Service Restart ==="
echo

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
    echo "This script needs sudo. Running with sudo..."
    exec sudo bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "1. Copying updated service file..."
cp "$SCRIPT_DIR/night-worker.service" /etc/systemd/system/
echo "   ✓ Service file copied"
echo

echo "2. Reloading systemd..."
systemctl daemon-reload
echo "   ✓ Daemon reloaded"
echo

echo "3. Restarting night-worker service..."
systemctl restart night-worker
echo "   ✓ Service restarted"
echo

echo "4. Checking service status..."
systemctl status night-worker --no-pager -l
echo

echo "=== Service restarted successfully! ==="
echo
echo "To watch logs in real-time, run:"
echo "  tail -f /var/log/night-worker.log"
echo
echo "Or check with journalctl:"
echo "  sudo journalctl -u night-worker -f"
