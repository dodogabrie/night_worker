#!/usr/bin/env bash
#
# End-to-end smoke test for night_worker.
# Creates a self-contained environment, builds the docker image,
# runs ralph_loop.py with MAX_ITERATIONS=1, and verifies output.
#
# Usage:  bash tests/smoke_test.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TMPDIR="$(mktemp -d /tmp/night-worker-smoke-XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== Night Worker Smoke Test ==="
echo "Temp dir: $TMPDIR"

# --- 1. Create directories ---
mkdir -p "$TMPDIR"/{input,output,nc_output,nc_logs,work,logs,.state/{queue,running,done,failed,trigger}}

# --- 2. Create a minimal input zip ---
PROJ_DIR="$TMPDIR/project"
mkdir -p "$PROJ_DIR"
cat > "$PROJ_DIR/PRD.md" <<'PRDEOF'
# Test Project
Just a smoke test.
PRDEOF

cat > "$PROJ_DIR/progress.txt" <<'PROGEOF'
- [ ] Task 1: say hello
PROGEOF

(cd "$PROJ_DIR" && zip -q "$TMPDIR/input/smoketest.zip" PRD.md progress.txt)
echo "Created input zip: smoketest.zip"

# --- 3. Create a fake claude script ---
FAKE_BIN="$TMPDIR/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/claude" <<'CLAUDEEOF'
#!/usr/bin/env bash
echo "Hello from fake claude!"
echo "RALPH_COMPLETE"
CLAUDEEOF
chmod +x "$FAKE_BIN/claude"

# --- 4. Create task prompt ---
cat > "$TMPDIR/task_prompt.txt" <<'PROMPTEOF'
You are a test agent. Just say hello.
PROMPTEOF

# --- 5. Build docker image ---
echo "Building docker image..."
if ! (cd "$PROJECT_DIR" && docker compose build worker 2>&1 | tail -5); then
    echo "FAIL: docker build failed"
    exit 1
fi

# --- 6. Create a trigger file ---
touch "$TMPDIR/trigger"

# --- 7. Run ralph_loop.py in background ---
echo "Starting ralph_loop.py..."
env \
    INPUT_DIR="$TMPDIR/input" \
    OUTPUT_DIR="$TMPDIR/nc_output" \
    STATE_DIR="$TMPDIR/.state" \
    TASK_PROMPT_FILE="$TMPDIR/task_prompt.txt" \
    WORK_DIR="$TMPDIR/work" \
    LOG_DIR="$TMPDIR/logs" \
    NEXTCLOUD_LOG_DIR="$TMPDIR/nc_logs" \
    LOG_SYNC_SECONDS=2 \
    POLL_SECONDS=2 \
    MAX_ITERATIONS=1 \
    MAX_SECONDS=30 \
    MAX_PARALLEL=1 \
    CONSUME_TRIGGER=true \
    START_TRIGGER_FILE="$TMPDIR/trigger" \
    KEEP_WORK_DIR=never \
    CLAUDE_CMD="$FAKE_BIN/claude" \
    python3 "$PROJECT_DIR/ralph_loop.py" &

RALPH_PID=$!

# --- 8. Wait for completion ---
echo "Waiting for job to complete (timeout 60s)..."
DEADLINE=$((SECONDS + 60))
PASSED=false

while [ $SECONDS -lt $DEADLINE ]; do
    if [ -f "$TMPDIR/.state/done/smoketest" ]; then
        PASSED=true
        break
    fi
    if [ -f "$TMPDIR/.state/failed/smoketest" ]; then
        echo "Job failed."
        break
    fi
    sleep 2
done

# --- 9. Stop ralph_loop ---
kill $RALPH_PID 2>/dev/null || true
wait $RALPH_PID 2>/dev/null || true

# --- 10. Verify results ---
echo ""
echo "=== Results ==="

FAIL=0

if [ "$PASSED" = true ]; then
    echo "PASS: done marker exists"
else
    echo "FAIL: done marker not found"
    FAIL=1
fi

if ls "$TMPDIR/nc_output"/smoketest*.zip 1>/dev/null 2>&1; then
    echo "PASS: output zip exists in nc_output"
else
    echo "FAIL: no output zip in nc_output"
    FAIL=1
fi

if [ -f "$TMPDIR/nc_logs/smoketest.log" ]; then
    echo "PASS: combined log exists"
else
    echo "FAIL: combined log not found"
    FAIL=1
fi

if [ -f "$TMPDIR/nc_logs/smoketest.status" ]; then
    STATUS=$(cat "$TMPDIR/nc_logs/smoketest.status")
    echo "PASS: status file exists ($STATUS)"
else
    echo "FAIL: status file not found"
    FAIL=1
fi

# Trigger should be consumed
if [ ! -f "$TMPDIR/trigger" ]; then
    echo "PASS: trigger file consumed"
else
    echo "FAIL: trigger file still exists"
    FAIL=1
fi

echo ""
if [ $FAIL -eq 0 ]; then
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo "=== SOME TESTS FAILED ==="
    echo ""
    echo "Debug: logs at $TMPDIR/logs/"
    # Don't delete tmpdir on failure for debugging
    trap - EXIT
    exit 1
fi
