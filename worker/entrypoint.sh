#!/bin/sh
# Entrypoint wrapper to ensure mount points exist in tmpfs
set -e

# Create mount point directories in tmpfs /job
mkdir -p /job/output

# Execute the actual worker
exec python3 /usr/local/bin/worker.py "$@"
