#!/bin/bash

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
tail -50 /var/log/night-worker.log
echo

# Check for output files
print_header "3. Output Files"
sg www-data -c "ls -lh /mnt/nextcloud-data/edoardo/files/RalphLoop/out/" 2>&1
echo

# Check status files
print_header "4. Job Status Files"
sg www-data -c "cat /mnt/nextcloud-data/edoardo/files/RalphLoop/out/*.status 2>/dev/null" || echo "No status files found"
echo

# Check state directory
print_header "5. Failed Jobs"
ls -l .state/failed/ 2>/dev/null | tail -10
echo

# Check job queue
print_header "6. Job Queue"
ls -l .state/queue/ 2>/dev/null | tail -10
echo

# Docker logs (if container is running)
print_header "7. Recent Docker Containers"
docker ps -a --filter "name=night_worker" --format "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}" | head -5
echo

print_header "Tips"
echo "To watch logs in real-time:"
echo "  tail -f /var/log/night-worker.log"
echo
echo "To check iteration logs inside a result zip:"
echo "  unzip -l /mnt/nextcloud-data/edoardo/files/RalphLoop/out/JOBNAME.result.zip"
echo "  unzip -p /mnt/nextcloud-data/edoardo/files/RalphLoop/out/JOBNAME.result.zip logs/iter-1.log"
echo
echo "To restart the service:"
echo "  ./restart-service.sh"
echo
