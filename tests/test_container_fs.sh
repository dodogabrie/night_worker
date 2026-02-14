#!/usr/bin/env bash
#
# Test that the worker container can read/write in the expected directories.
#
# The container runs with:
#   - read_only: true (root fs)
#   - user: 1000:1000
#   - tmpfs at /tmp and /job
#   - host mounts for input (ro), output, and /tmp/work
#
# This test builds the image and runs a probe script inside the container
# to verify filesystem access.
#
# Usage:  bash tests/test_container_fs.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TMPDIR="$(mktemp -d /tmp/nw-fs-test-XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== Container Filesystem Permissions Test ==="

# --- Setup host dirs (simulating what ralph_loop.py creates) ---
mkdir -p "$TMPDIR"/{input,output,tmp}

# Create a minimal valid zip as input
mkdir -p "$TMPDIR/project"
echo "test content" > "$TMPDIR/project/test.txt"
(cd "$TMPDIR/project" && zip -q "$TMPDIR/input/input.zip" test.txt)

# Create a task prompt file
echo "test prompt" > "$TMPDIR/task_prompt.txt"

# --- Build image ---
echo "Building worker image..."
if ! (cd "$PROJECT_DIR" && docker compose build worker 2>&1 | tail -3); then
    echo "FAIL: docker build failed"
    exit 1
fi

# --- Run probe script inside container ---
echo "Running filesystem probe..."

FAIL=0

# The probe script tests each filesystem operation the worker needs
PROBE_SCRIPT='
import os, sys, zipfile, shutil
from pathlib import Path

errors = []

def check(label, fn):
    try:
        fn()
        print(f"  PASS: {label}")
    except Exception as e:
        print(f"  FAIL: {label} — {e}")
        errors.append(label)

uid = os.getuid()
gid = os.getgid()
print(f"Running as uid={uid} gid={gid}")

# 1. Read input zip (mounted read-only)
check("read /job/input.zip", lambda: Path("/job/input.zip").read_bytes())

# 2. Read task prompt (mounted read-only)
check("read /job/task_prompt.txt", lambda: Path("/job/task_prompt.txt").read_text())

# 3. Extract zip to /tmp/work/project
def extract_zip():
    project = Path("/tmp/work/project")
    project.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile("/job/input.zip") as zf:
        zf.extractall(project)
    assert (project / "test.txt").read_text().strip() == "test content"
check("extract zip to /tmp/work/project", extract_zip)

# 4. Create log dir and write iter log
def write_log():
    log_dir = Path("/tmp/work/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "iter-1.log"
    log_file.write_text("test iteration output\n")
    assert log_file.read_text() == "test iteration output\n"
check("write iter log to /tmp/work/logs/", write_log)

# 5. Write output zip to /job/output
def write_output():
    out = Path("/job/output")
    out.mkdir(parents=True, exist_ok=True)
    zip_tmp = out / "test_v1.partial.zip"
    zip_final = out / "test_v1.zip"
    with zipfile.ZipFile(zip_tmp, "w") as zf:
        zf.writestr("result.txt", "done")
    zip_tmp.rename(zip_final)
    assert zip_final.exists()
    assert not zip_tmp.exists()
check("write output zip to /job/output", write_output)

# 6. Write status file
def write_status():
    status = Path("/job/output/test.status")
    status.write_text("done\n")
    assert status.read_text() == "done\n"
check("write status to /job/output", write_status)

# 7. Create staging dir for result archive
def staging_dir():
    staging = Path("/tmp/work/result")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "metadata.txt").write_text("job_id=test\n")
    project_stage = staging / "project"
    shutil.copytree("/tmp/work/project", project_stage)
    assert (project_stage / "test.txt").exists()
check("create staging dir in /tmp/work/result", staging_dir)

# 8. Verify root fs is read-only
def check_readonly():
    try:
        Path("/usr/local/bin/probe.txt").write_text("nope")
        errors.append("root fs writable!")  # should not happen
    except OSError:
        pass  # expected: read-only
check("root filesystem is read-only", check_readonly)

if errors:
    print(f"\n{len(errors)} check(s) failed")
    sys.exit(1)
else:
    print("\nAll checks passed")
'

# Run with the same constraints as docker-compose.yml
docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm \
    -v "$TMPDIR/input/input.zip:/job/input.zip:ro" \
    -v "$TMPDIR/task_prompt.txt:/job/task_prompt.txt:ro" \
    -v "$TMPDIR/output:/job/output" \
    -v "$TMPDIR/tmp:/tmp/work" \
    --entrypoint python3 \
    worker -c "$PROBE_SCRIPT"

RC=$?

echo ""
if [ $RC -eq 0 ]; then
    echo "=== CONTAINER FS TEST PASSED ==="
else
    echo "=== CONTAINER FS TEST FAILED ==="
    FAIL=1
fi

exit $FAIL
