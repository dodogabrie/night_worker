#!/usr/bin/env bash
#
# Test that the claude CLI works inside the worker container.
# Sends a trivial prompt and checks for a response.
#
# Usage:  bash tests/test_claude_cli.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse .env the same way ralph_loop.py does (no bash source — values can have spaces)
while IFS= read -r line; do
    line="${line## }"
    line="${line%% }"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key## }"
    key="${key%% }"
    value="${value## }"
    value="${value%% }"
    export "$key=$value"
done < "$PROJECT_DIR/.env"

echo "=== Claude CLI Test ==="
echo "CLAUDE_ARGS: $CLAUDE_ARGS"
echo "CLAUDE_CREDS_VOLUME: ${CLAUDE_CREDS_VOLUME:-not set}"

# Build image if needed
echo "Building worker image..."
if ! (cd "$PROJECT_DIR" && docker compose build worker 2>&1 | tail -3); then
    echo "FAIL: docker build failed"
    exit 1
fi

# Run a minimal prompt inside the container
echo ""
echo "Sending test prompt: 'Reply with exactly: PING_OK'"
echo ""

# shellcheck disable=SC2086
RESPONSE=$(docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm \
    --entrypoint "" \
    worker \
    claude $CLAUDE_ARGS -p "Reply with exactly one word: PING_OK. Nothing else." \
    2>&1) || {
    echo "FAIL: claude command exited with error"
    echo "Output: $RESPONSE"
    exit 1
}

echo "Response: $RESPONSE"
echo ""

if echo "$RESPONSE" | grep -q "PING_OK"; then
    echo "=== CLAUDE CLI TEST PASSED ==="
    echo "Model responds, API credentials work."
else
    echo "=== CLAUDE CLI TEST FAILED ==="
    echo "Expected PING_OK in response but got something else."
    echo "This might still be OK — the model responded, just not exactly as asked."
    exit 1
fi
