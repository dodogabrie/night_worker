#!/bin/bash

# Default log dir — override with LOG_DIR env var or .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    LOG_DIR=$(grep '^LOG_DIR=' "$SCRIPT_DIR/.env" | cut -d= -f2)
fi
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"

echo "=== Night Worker Log Checker ==="
echo

# Function to print section header
print_header() {
    echo
    echo "===================="
    echo "$1"
    echo "===================="
    echo
}

# Check service status
print_header "1. Service Status"
systemctl is-active night-worker && echo "Service: RUNNING" || echo "Service: STOPPED"
echo

# Main worker log (last 50 lines)
print_header "2. Main Worker Log (last 50 lines)"
if [ -f "$LOG_DIR/night-worker.log" ]; then
    tail -50 "$LOG_DIR/night-worker.log"
else
    echo "No log file found at $LOG_DIR/night-worker.log"
fi
echo

# Per-job logs
print_header "3. Job Logs"
if [ -d "$LOG_DIR/jobs" ]; then
    ls -lht "$LOG_DIR/jobs/" 2>/dev/null | head -10
else
    echo "No job logs directory"
fi
echo

# Check for output files
print_header "4. Output Files"
sg www-data -c "ls -lh /mnt/nextcloud-data/edoardo/files/RalphLoop/out/" 2>&1
echo

# Check status files
print_header "5. Job Status Files"
sg www-data -c "cat /mnt/nextcloud-data/edoardo/files/RalphLoop/out/*.status 2>/dev/null" || echo "No status files found"
echo

# Nextcloud live logs
print_header "6. Nextcloud Live Logs"
sg www-data -c "ls -lht /mnt/nextcloud-data/edoardo/files/RalphLoop/logs/" 2>&1 | head -10
echo

# Check state directory
print_header "7. Failed Jobs"
ls -l "$SCRIPT_DIR/.state/failed/" 2>/dev/null | tail -10
echo

# Check job queue
print_header "8. Job Queue"
ls -l "$SCRIPT_DIR/.state/queue/" 2>/dev/null | tail -10
echo

# Docker logs (if container is running)
print_header "9. Recent Docker Containers"
docker ps -a --filter "name=night_worker" --format "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}" | head -5
echo

print_header "Tips"
echo "To watch logs in real-time:"
echo "  tail -f $LOG_DIR/night-worker.log"
echo
echo "To read a specific job log:"
echo "  cat $LOG_DIR/jobs/<job_id>.log"
echo
echo "To check Nextcloud live logs:"
echo "  cat /mnt/nextcloud-data/edoardo/files/RalphLoop/logs/<job_id>.log"
echo "  cat /mnt/nextcloud-data/edoardo/files/RalphLoop/logs/<job_id>.status"
echo
echo "To check iteration logs inside a result zip:"
echo "  unzip -l /mnt/nextcloud-data/edoardo/files/RalphLoop/out/JOBNAME.result.zip"
echo "  unzip -p /mnt/nextcloud-data/edoardo/files/RalphLoop/out/JOBNAME.result.zip logs/iter-1.log"
echo
echo "To restart the service:"
echo "  ./restart-service.sh"
echo
