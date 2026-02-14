#!/bin/bash
set -e

echo "=== Night Worker Permission Setup ==="
echo

# Check if running with sudo
if [ "$EUID" -ne 0 ]; then
    echo "This script needs sudo. Running with sudo..."
    exec sudo bash "$0" "$@"
fi

USER="edoardo"
BASE_DIR="/mnt/nextcloud-data/edoardo/files/RalphLoop"

echo "1. Adding user $USER to www-data group..."
usermod -a -G www-data "$USER"
echo "   ✓ User added to www-data group"
echo

echo "2. Checking if directories exist..."
if [ -d "$BASE_DIR" ]; then
    echo "   ✓ $BASE_DIR exists"
else
    echo "   Creating $BASE_DIR..."
    mkdir -p "$BASE_DIR"/{in,out,logs}
fi

# Ensure subdirectories exist
mkdir -p "$BASE_DIR"/{in,out,logs}
echo "   ✓ All subdirectories exist"
echo

echo "3. Setting permissions (www-data:www-data, 770)..."
chown -R www-data:www-data "$BASE_DIR"
chmod -R 770 "$BASE_DIR"
echo "   ✓ Permissions set"
echo

echo "4. Creating trigger files..."
touch "$BASE_DIR/in/night.md"
touch "$BASE_DIR/in/start.md"
chown www-data:www-data "$BASE_DIR/in/night.md" "$BASE_DIR/in/start.md"
echo "   ✓ Trigger files created"
echo

echo "5. Verifying setup..."
ls -la "$BASE_DIR"
echo
ls -la "$BASE_DIR/in/"
echo

echo "=== Setup Complete! ==="
echo
echo "IMPORTANT: User $USER needs to logout and login again"
echo "          (or run: newgrp www-data)"
echo "          for group membership to take effect."
echo
echo "After that, start the service with:"
echo "  sudo systemctl restart night-worker"
echo "  sudo systemctl status night-worker"
