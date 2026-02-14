#!/bin/bash
sudo systemctl restart night-worker
echo "Service restarted. Watching logs..."
echo "Press Ctrl+C to stop watching."
echo
tail -f /var/log/night-worker.log
