#!/bin/bash
set -e

echo "=== Fixing Input Folder Permissions ==="
echo

INPUT_DIR="/mnt/nextcloud-data/edoardo/files/RalphLoop/in"

echo "1. Fixing folder permissions (775)..."
sudo chmod 775 "$INPUT_DIR"
echo "   ✓ Folder permissions updated"
echo

echo "2. Fixing zip file permissions (664)..."
sudo chmod 664 "$INPUT_DIR"/*.zip 2>/dev/null || echo "   (No zip files found)"
echo "   ✓ Zip file permissions updated"
echo

echo "3. Verifying permissions..."
echo "Folder:"
ls -ld "$INPUT_DIR"
echo
echo "Files:"
ls -lh "$INPUT_DIR"/*.zip 2>/dev/null | head -5 || echo "   (No zip files)"
echo

echo "=== Permissions fixed! ==="
echo "Now run: ./trigger-loop.sh"
