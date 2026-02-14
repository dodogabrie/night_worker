#!/usr/bin/env python3
import logging
import logging.handlers
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_logger = logging.getLogger("night-worker")


def setup_logging(log_dir: Path) -> None:
    """Configure root logger with stdout + rotating file handlers."""
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "jobs").mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    fmt.converter = time.gmtime

    _logger.setLevel(logging.DEBUG)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(fmt)
    _logger.addHandler(stdout_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "night-worker.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    _logger.addHandler(file_handler)


def _make_job_logger(log_dir: Path, job_id: str) -> logging.Logger:
    """Create a per-job file logger that writes to LOG_DIR/jobs/<job_id>.log."""
    job_logger = logging.getLogger(f"night-worker.job.{job_id}")
    job_logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    fmt.converter = time.gmtime
    fh = logging.FileHandler(
        log_dir / "jobs" / f"{job_id}.log",
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    job_logger.addHandler(fh)
    return job_logger


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_env_file(env_file: Path) -> None:
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------


def resolve_trigger_path(script_dir: Path) -> Path | None:
    """
    Optional start trigger gate.

    If START_TRIGGER_FILE is set, the loop will only process jobs when the file exists.
    If START_TRIGGER_DIR is set, it is used as base for relative START_TRIGGER_FILE.
    """
    rel = os.environ.get("START_TRIGGER_FILE", "").strip()
    if not rel:
        return None

    base = os.environ.get("START_TRIGGER_DIR", "").strip()
    if base:
        base_dir = Path(base)
    else:
        base_dir = script_dir

    p = Path(rel)
    if not p.is_absolute():
        p = base_dir / p
    return p


def resolve_persistent_trigger_path(script_dir: Path) -> Path | None:
    """
    Optional persistent trigger gate.

    If PERSISTENT_TRIGGER_FILE is set, the loop will process jobs only when the file's
    mtime has increased since the last handled run (e.g. cron `touch` at 1am).
    If PERSISTENT_TRIGGER_DIR is set, it is used as base for relative PERSISTENT_TRIGGER_FILE.
    """
    rel = os.environ.get("PERSISTENT_TRIGGER_FILE", "").strip()
    if not rel:
        return None

    base = os.environ.get("PERSISTENT_TRIGGER_DIR", "").strip()
    if base:
        base_dir = Path(base)
    else:
        base_dir = script_dir

    p = Path(rel)
    if not p.is_absolute():
        p = base_dir / p
    return p


def read_float(path: Path) -> float:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0.0


def write_float(path: Path, value: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def should_fire_persistent_trigger(persistent_trigger: Path, state_dir: Path) -> bool:
    if not persistent_trigger.exists():
        return False
    state_file = state_dir / "trigger" / f"{persistent_trigger.name}.mtime"
    last_handled = read_float(state_file)
    try:
        current = persistent_trigger.stat().st_mtime
    except OSError:
        return False
    return current > last_handled


def mark_persistent_trigger_handled(persistent_trigger: Path, state_dir: Path) -> None:
    state_file = state_dir / "trigger" / f"{persistent_trigger.name}.mtime"
    try:
        current = persistent_trigger.stat().st_mtime
    except OSError:
        return
    write_float(state_file, current)


def consume_start_trigger_if_needed(
    trigger_path: Path | None,
    consume_trigger: bool,
) -> None:
    if not consume_trigger or trigger_path is None or not trigger_path.exists():
        return
    try:
        trigger_path.unlink()
        _logger.info("[trigger] Consumed start trigger file: %s", trigger_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------


def ensure_default_prompt(task_prompt_file: Path) -> None:
    if task_prompt_file.exists():
        return
    task_prompt_file.write_text(
        "\n".join(
            [
                "Sei un coding agent autonomo in modalita Ralph Wiggum.",
                "Leggi prd.json e progress.txt nel progetto.",
                "Completa un solo task per iterazione, iniziando dal piu prioritario con stato non completato.",
                "Esegui i check/test del progetto e aggiorna progress.txt con risultato e prossimi passi.",
                "Mantieni modifiche piccole e atomiche.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _logger.info("[scan] Created default task prompt at %s", task_prompt_file)


def claim_job(state_queue: Path, running_dir: Path, zip_path: Path) -> str | None:
    job_id = zip_path.stem
    claim_path = state_queue / f"{job_id}.claimed"
    try:
        os.symlink(str(zip_path), str(claim_path))
        return job_id
    except FileExistsError:
        # Recover stale claims left behind by an interrupted orchestrator.
        if not (running_dir / job_id).exists():
            try:
                claim_path.unlink(missing_ok=True)
                os.symlink(str(zip_path), str(claim_path))
                return job_id
            except OSError:
                return None
        return None


def running_jobs_count(running_dir: Path) -> int:
    return sum(1 for p in running_dir.iterdir() if p.is_file())


def find_latest_version_zip(output_dir: Path, job_id: str) -> tuple[Path | None, int]:
    """
    Find the latest <job_id>_vN.zip in output_dir.
    Returns (path, N). If none found, returns (None, 0).
    """
    best_n = 0
    best_path: Path | None = None
    prefix = f"{job_id}_v"
    for p in output_dir.glob(f"{job_id}_v*.zip"):
        name = p.name
        if not name.startswith(prefix):
            continue
        try:
            n_part = name[len(prefix) : -len(".zip")]
            n = int(n_part)
        except ValueError:
            continue
        if n > best_n:
            best_n = n
            best_path = p
    return best_path, best_n


def ensure_worker_writable_dir(path: Path, jlog: logging.Logger) -> None:
    """
    Ensure bind-mounted dirs are writable by the worker container user.
    The orchestrator may run as root and create these dirs as 0755 root:root.
    Worker runs as UID 1000 and needs write access to /job/output and /tmp/work.
    """
    try:
        path.chmod(0o777)
    except OSError as e:
        jlog.warning("[job] failed to chmod %s to 0777: %s", path, e)


# ---------------------------------------------------------------------------
# Atomic copy helper
# ---------------------------------------------------------------------------


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy src to dst using a .tmp intermediate + rename for crash safety."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.rename(dst)


# ---------------------------------------------------------------------------
# Nextcloud sync helpers
# ---------------------------------------------------------------------------


def sync_iter_logs(
    work_logs_dir: Path,
    nc_log_path: Path,
    synced_offsets: dict[str, int],
    jlog: logging.Logger,
) -> None:
    """
    Scan WORK_DIR/<job_id>/tmp/logs/ for iter-N.log files.
    Build/append to a combined rolling log at nc_log_path.
    Track bytes already synced per file to avoid re-reading.
    """
    if not work_logs_dir.is_dir():
        return

    iter_files = sorted(work_logs_dir.glob("iter-*.log"))
    if not iter_files:
        return

    nc_log_path.parent.mkdir(parents=True, exist_ok=True)

    with nc_log_path.open("a", encoding="utf-8") as combined:
        for iter_file in iter_files:
            fname = iter_file.name
            offset = synced_offsets.get(fname, 0)
            try:
                size = iter_file.stat().st_size
            except OSError:
                continue
            if size <= offset:
                continue

            # If this is the first time we see this file, write a header
            if offset == 0:
                # Extract iteration number for the header
                try:
                    mtime = datetime.fromtimestamp(
                        iter_file.stat().st_mtime, tz=timezone.utc
                    )
                    ts_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
                except OSError:
                    ts_str = "unknown"
                iter_num = fname.replace("iter-", "").replace(".log", "")
                combined.write(
                    f"\n=== Iteration {iter_num} started {ts_str} ===\n\n"
                )

            with iter_file.open("r", encoding="utf-8", errors="ignore") as f:
                f.seek(offset)
                new_data = f.read()
                combined.write(new_data)

            synced_offsets[fname] = size

    jlog.debug("[sync] synced iteration logs to %s", nc_log_path)


def write_nc_status(
    nc_status_path: Path,
    state: str,
    detail: str,
) -> None:
    """Write a one-liner status file to Nextcloud."""
    nc_status_path.parent.mkdir(parents=True, exist_ok=True)
    nc_status_path.write_text(f"{state} | {detail}\n", encoding="utf-8")


def sync_output_zips(
    local_output_dir: Path,
    nc_output_dir: Path,
    synced_zips: set[str],
    jlog: logging.Logger,
) -> None:
    """Copy new .zip files (not .partial.zip) from local output to Nextcloud."""
    if not local_output_dir.is_dir():
        return
    for zp in local_output_dir.iterdir():
        if not zp.name.endswith(".zip"):
            continue
        if zp.name.endswith(".partial.zip"):
            continue
        if zp.name in synced_zips:
            continue
        try:
            atomic_copy(zp, nc_output_dir / zp.name)
            synced_zips.add(zp.name)
            jlog.info("[sync] copied output %s to Nextcloud", zp.name)
        except OSError as e:
            jlog.warning("[sync] failed to copy %s: %s", zp.name, e)


def sync_output_status_files(
    local_output_dir: Path,
    nc_output_dir: Path,
    jlog: logging.Logger,
) -> None:
    """Copy .status files from local output to Nextcloud."""
    if not local_output_dir.is_dir():
        return
    for sf in local_output_dir.glob("*.status"):
        try:
            atomic_copy(sf, nc_output_dir / sf.name)
        except OSError as e:
            jlog.warning("[sync] failed to copy status %s: %s", sf.name, e)


def run_post_sync_hook(
    command: str,
    timeout_seconds: int,
    *,
    job_id: str,
    nc_output_dir: Path,
    nc_log_dir: Path | None,
    jlog: logging.Logger,
) -> None:
    """
    Optionally run a post-sync hook command after each job.
    Useful for external indexers (e.g., Nextcloud occ files:scan).
    """
    if not command:
        return

    env = os.environ.copy()
    env["JOB_ID"] = job_id
    env["NC_OUTPUT_DIR"] = str(nc_output_dir)
    env["NC_LOG_DIR"] = str(nc_log_dir) if nc_log_dir else ""

    jlog.info("[hook] running POST_SYNC_HOOK_CMD for job %s", job_id)
    try:
        result = subprocess.run(
            command,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        jlog.warning("[hook] POST_SYNC_HOOK_CMD timed out after %ds", timeout_seconds)
        return
    except OSError as e:
        jlog.warning("[hook] POST_SYNC_HOOK_CMD failed to start: %s", e)
        return

    if result.stdout.strip():
        jlog.info("[hook] stdout: %s", result.stdout.strip())
    if result.stderr.strip():
        jlog.warning("[hook] stderr: %s", result.stderr.strip())

    if result.returncode != 0:
        jlog.warning("[hook] POST_SYNC_HOOK_CMD exited with rc=%d", result.returncode)
    else:
        jlog.info("[hook] POST_SYNC_HOOK_CMD completed")


# ---------------------------------------------------------------------------
# start_job — rewritten with Popen + poll loop
# ---------------------------------------------------------------------------


def start_job(
    script_dir: Path,
    nc_output_dir: Path,
    task_prompt_file: Path,
    state_dir: Path,
    job_id: str,
    zip_path: Path,
    *,
    work_dir: Path,
    log_dir: Path,
    nc_log_dir: Path | None,
    log_sync_seconds: int,
    keep_work_dir: str,
    post_sync_hook_cmd: str,
    post_sync_hook_timeout_seconds: int,
    keep_failed_marker: bool,
    version_offset: int = 0,
) -> bool:
    running_marker = state_dir / "running" / job_id
    done_marker = state_dir / "done" / job_id
    failed_marker = state_dir / "failed" / job_id

    jlog = _make_job_logger(log_dir, job_id)

    # 1. Create local work dir
    job_work = work_dir / job_id
    job_input = job_work / "input"
    job_output = job_work / "output"
    job_tmp = job_work / "tmp"
    for d in (job_input, job_output, job_tmp):
        d.mkdir(parents=True, exist_ok=True)
        ensure_worker_writable_dir(d, jlog)

    _logger.info("[job] Starting job %s", job_id)
    jlog.info("[job] Starting job %s (zip=%s, version_offset=%d)", job_id, zip_path.name, version_offset)
    running_marker.touch()

    # 2. Copy input zip to local work dir
    local_input_zip = job_input / "input.zip"
    try:
        shutil.copy2(zip_path, local_input_zip)
        jlog.info("[job] Copied input zip to %s", local_input_zip)
    except OSError as e:
        jlog.error("[job] Failed to copy input zip: %s", e)
        _logger.error("[job] Job %s failed: cannot copy input zip: %s", job_id, e)
        running_marker.unlink(missing_ok=True)
        if keep_failed_marker:
            failed_marker.touch()
        return False

    # 3. Build docker command — mount LOCAL dirs only
    env = os.environ.copy()
    start_time = time.monotonic()
    command = [
        "docker",
        "compose",
        "-f",
        str(script_dir / "docker-compose.yml"),
        "run",
        "--rm",
        "-e", f"JOB_ID={job_id}",
        "-e", "INPUT_ZIP=/job/input.zip",
        "-e", "OUTPUT_DIR=/job/output",
        "-e", "TASK_PROMPT_FILE=/job/task_prompt.txt",
        "-e", f"MAX_ITERATIONS={env.get('MAX_ITERATIONS', '8')}",
        "-e", f"MAX_SECONDS={env.get('MAX_SECONDS', '3600')}",
        "-e", f"ITER_TIMEOUT_SECONDS={env.get('ITER_TIMEOUT_SECONDS', '600')}",
        "-e", f"SOFT_STOP_MARGIN_SECONDS={env.get('SOFT_STOP_MARGIN_SECONDS', '90')}",
        "-e", f"CLAUDE_CMD={env.get('CLAUDE_CMD', 'claude')}",
        "-e", f"CLAUDE_ARGS={env.get('CLAUDE_ARGS', '--print')}",
        "-e", f"CLAUDE_INPUT_MODE={env.get('CLAUDE_INPUT_MODE', 'stdin')}",
        "-e", f"COMPLETE_SIGNAL={env.get('COMPLETE_SIGNAL', 'RALPH_COMPLETE')}",
        "-e", f"MAX_CONSECUTIVE_TRANSIENT_ERRORS={env.get('MAX_CONSECUTIVE_TRANSIENT_ERRORS', '4')}",
        "-e", f"TRANSIENT_BACKOFF_SECONDS={env.get('TRANSIENT_BACKOFF_SECONDS', '20')}",
        "-e", f"ZIP_CHAIN_MODE={env.get('ZIP_CHAIN_MODE', '0')}",
        "-e", f"NEXT_INSTRUCTION_FILE={env.get('NEXT_INSTRUCTION_FILE', 'next_instruction.txt')}",
        "-e", f"VERSION_OFFSET={version_offset}",
        "-v", f"{local_input_zip}:/job/input.zip:ro",
        "-v", f"{task_prompt_file}:/job/task_prompt.txt:ro",
        "-v", f"{job_output}:/job/output",
        "-v", f"{job_tmp}:/tmp/work",
        "worker",
    ]

    jlog.debug("[job] Command: %s", " ".join(command))

    # 4. Launch with Popen
    proc = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    jlog.info("[job] Container started (pid=%d)", proc.pid)

    # 5. Poll loop — sync logs and output while container runs
    synced_offsets: dict[str, int] = {}
    synced_zips: set[str] = set()
    work_logs_dir = job_tmp / "logs"
    nc_log_path = nc_log_dir / f"{job_id}.log" if nc_log_dir else None
    nc_status_path = nc_log_dir / f"{job_id}.status" if nc_log_dir else None

    while proc.poll() is None:
        time.sleep(log_sync_seconds)

        elapsed = int(time.monotonic() - start_time)
        elapsed_str = _format_elapsed(elapsed)

        # Count iterations from log files
        iter_count = len(list(work_logs_dir.glob("iter-*.log"))) if work_logs_dir.is_dir() else 0
        max_iter = env.get("MAX_ITERATIONS", "8")

        if nc_log_path:
            try:
                sync_iter_logs(work_logs_dir, nc_log_path, synced_offsets, jlog)
            except OSError as e:
                jlog.warning("[sync] log sync error: %s", e)

        if nc_status_path:
            try:
                write_nc_status(
                    nc_status_path,
                    "running",
                    f"iter {iter_count}/{max_iter} | elapsed {elapsed_str}",
                )
            except OSError as e:
                jlog.warning("[sync] status write error: %s", e)

        # Sync output zips mid-run (for zip-chain crash safety)
        try:
            sync_output_zips(job_output, nc_output_dir, synced_zips, jlog)
        except OSError as e:
            jlog.warning("[sync] output sync error: %s", e)

    rc = proc.returncode
    elapsed = int(time.monotonic() - start_time)
    elapsed_str = _format_elapsed(elapsed)

    jlog.info("[job] Container exited (rc=%d, elapsed=%s)", rc, elapsed_str)

    # 6. Final sync
    if nc_log_path:
        try:
            sync_iter_logs(work_logs_dir, nc_log_path, synced_offsets, jlog)
        except OSError as e:
            jlog.warning("[sync] final log sync error: %s", e)

    try:
        sync_output_zips(job_output, nc_output_dir, synced_zips, jlog)
        sync_output_status_files(job_output, nc_output_dir, jlog)
    except OSError as e:
        jlog.warning("[sync] final output sync error: %s", e)

    # Determine final status for Nextcloud status file
    iter_count = len(list(work_logs_dir.glob("iter-*.log"))) if work_logs_dir.is_dir() else 0
    worker_status: str | None = None
    worker_status_file = job_output / f"{job_id}.status"
    if worker_status_file.is_file():
        try:
            worker_status = worker_status_file.read_text(encoding="utf-8").strip().lower()
        except OSError as e:
            jlog.warning("[job] failed to read worker status file %s: %s", worker_status_file, e)

    running_marker.unlink(missing_ok=True)

    success = rc == 0 and worker_status not in {"failed"}

    if success:
        done_marker.touch()
        _logger.info("[job] Job %s completed (%d iterations, %s)", job_id, iter_count, elapsed_str)
        jlog.info(
            "[job] Job %s completed (%d iterations, %s, worker_status=%s)",
            job_id,
            iter_count,
            elapsed_str,
            worker_status or "unknown",
        )
        if nc_status_path:
            try:
                write_nc_status(nc_status_path, "done", f"{iter_count} iterations, {elapsed_str}")
            except OSError:
                pass
    else:
        if keep_failed_marker:
            failed_marker.touch()
        else:
            failed_marker.unlink(missing_ok=True)
        _logger.error(
            "[job] Job %s failed (rc=%d, %d iterations, %s, worker_status=%s)",
            job_id,
            rc,
            iter_count,
            elapsed_str,
            worker_status or "unknown",
        )
        jlog.error(
            "[job] Job %s failed (rc=%d, %d iterations, %s, worker_status=%s)",
            job_id,
            rc,
            iter_count,
            elapsed_str,
            worker_status or "unknown",
        )
        if nc_status_path:
            try:
                detail = f"iter {iter_count}, {elapsed_str}"
                if worker_status:
                    detail = f"{detail}, worker_status={worker_status}"
                write_nc_status(nc_status_path, f"failed (rc={rc})", detail)
            except OSError:
                pass

    run_post_sync_hook(
        post_sync_hook_cmd,
        post_sync_hook_timeout_seconds,
        job_id=job_id,
        nc_output_dir=nc_output_dir,
        nc_log_dir=nc_log_dir,
        jlog=jlog,
    )

    # 7. Cleanup
    _cleanup_work_dir(job_work, success, keep_work_dir, jlog)

    # Close job logger handlers
    for h in jlog.handlers[:]:
        h.close()
        jlog.removeHandler(h)

    return success


def _format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


def _cleanup_work_dir(
    job_work: Path,
    success: bool,
    keep_work_dir: str,
    jlog: logging.Logger,
) -> None:
    if keep_work_dir == "always":
        jlog.debug("[job] Keeping work dir (KEEP_WORK_DIR=always): %s", job_work)
        return
    if keep_work_dir == "never":
        shutil.rmtree(job_work, ignore_errors=True)
        jlog.debug("[job] Removed work dir (KEEP_WORK_DIR=never): %s", job_work)
        return
    # Default: on_failure
    if success:
        shutil.rmtree(job_work, ignore_errors=True)
        jlog.debug("[job] Removed work dir (success): %s", job_work)
    else:
        jlog.info("[job] Keeping work dir for debugging (failure): %s", job_work)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    env_file = Path(os.environ.get("ENV_FILE", str(script_dir / ".env")))
    load_env_file(env_file)

    input_dir = Path(os.environ.get("INPUT_DIR", "/srv/nextcloud/night_worker/input"))
    nc_output_dir = Path(os.environ.get("OUTPUT_DIR", "/srv/nextcloud/night_worker/output"))
    state_dir = Path(os.environ.get("STATE_DIR", str(script_dir / ".state")))
    task_prompt_file = Path(os.environ.get("TASK_PROMPT_FILE", str(script_dir / "task_prompt.txt")))
    work_dir = Path(os.environ.get("WORK_DIR", str(script_dir / "work")))
    log_dir = Path(os.environ.get("LOG_DIR", str(script_dir / "logs")))
    nc_log_dir_raw = os.environ.get("NEXTCLOUD_LOG_DIR", "").strip()
    nc_log_dir = Path(nc_log_dir_raw) if nc_log_dir_raw else None
    log_sync_seconds = env_int("LOG_SYNC_SECONDS", 10)
    keep_work_dir = os.environ.get("KEEP_WORK_DIR", "on_failure").strip().lower()
    post_sync_hook_cmd = os.environ.get("POST_SYNC_HOOK_CMD", "").strip()
    post_sync_hook_timeout_seconds = env_int("POST_SYNC_HOOK_TIMEOUT_SECONDS", 180)
    stop_loop_on_job_failure = env_bool("STOP_LOOP_ON_JOB_FAILURE", False)
    keep_failed_marker = env_bool("KEEP_FAILED_MARKER", True)
    poll_seconds = env_int("POLL_SECONDS", 20)
    max_parallel = env_int("MAX_PARALLEL", 1)
    consume_trigger = env_bool("CONSUME_TRIGGER", True)
    trigger_path = resolve_trigger_path(script_dir)
    persistent_trigger_path = resolve_persistent_trigger_path(script_dir)
    strict_single_zip = env_bool("STRICT_SINGLE_ZIP_CONTRACT", False)
    strict_allow_versioned_inputs = env_bool("STRICT_ALLOW_VERSIONED_INPUTS", False)

    # Initialize logging
    setup_logging(log_dir)

    for path in [
        input_dir,
        nc_output_dir,
        state_dir / "queue",
        state_dir / "running",
        state_dir / "done",
        state_dir / "failed",
        state_dir / "trigger",
        work_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    if nc_log_dir:
        nc_log_dir.mkdir(parents=True, exist_ok=True)

    ensure_default_prompt(task_prompt_file)
    if trigger_path is not None or persistent_trigger_path is not None:
        _logger.info(
            "[trigger] Ralph loop started (trigger-gated). "
            "Start trigger: %s  Persistent trigger: %s  "
            "Input: %s  Output: %s",
            trigger_path or "disabled",
            persistent_trigger_path or "disabled",
            input_dir,
            nc_output_dir,
        )
    else:
        _logger.info("[trigger] Ralph loop started. Input: %s  Output: %s", input_dir, nc_output_dir)

    while True:
        start_armed = trigger_path is not None and trigger_path.exists()
        persistent_armed = (
            persistent_trigger_path is not None and should_fire_persistent_trigger(persistent_trigger_path, state_dir)
        )
        if (trigger_path is not None or persistent_trigger_path is not None) and not (start_armed or persistent_armed):
            time.sleep(poll_seconds)
            continue

        if running_jobs_count(state_dir / "running") >= max_parallel:
            time.sleep(poll_seconds)
            continue

        zips = sorted(input_dir.glob("*.zip"))

        if strict_single_zip:
            if len(zips) == 0:
                time.sleep(poll_seconds)
                continue
            if len(zips) != 1:
                raise SystemExit(
                    f"STRICT_SINGLE_ZIP_CONTRACT violation: expected exactly 1 zip in {input_dir}, found {len(zips)}"
                )

            zip_path = zips[0]
            job_id = zip_path.stem
            if (not strict_allow_versioned_inputs) and "_v" in zip_path.name:
                raise SystemExit(
                    f"STRICT_SINGLE_ZIP_CONTRACT violation: versioned zip in input dir not allowed: {zip_path.name}"
                )

            latest_zip, latest_n = find_latest_version_zip(nc_output_dir, job_id)
            input_zip_for_run = latest_zip or zip_path
            version_offset = latest_n

            if running_jobs_count(state_dir / "running") < max_parallel:
                if persistent_armed and persistent_trigger_path is not None:
                    mark_persistent_trigger_handled(persistent_trigger_path, state_dir)
                success = start_job(
                    script_dir,
                    nc_output_dir,
                    task_prompt_file,
                    state_dir,
                    job_id,
                    input_zip_for_run,
                    work_dir=work_dir,
                    log_dir=log_dir,
                    nc_log_dir=nc_log_dir,
                    log_sync_seconds=log_sync_seconds,
                    keep_work_dir=keep_work_dir,
                    post_sync_hook_cmd=post_sync_hook_cmd,
                    post_sync_hook_timeout_seconds=post_sync_hook_timeout_seconds,
                    keep_failed_marker=keep_failed_marker,
                    version_offset=version_offset,
                )
                if (not success) and stop_loop_on_job_failure:
                    consume_start_trigger_if_needed(trigger_path, consume_trigger)
                    _logger.error(
                        "[loop] stopping due to job failure (STOP_LOOP_ON_JOB_FAILURE=1, job=%s)",
                        job_id,
                    )
                    raise SystemExit(1)

            if start_armed and consume_trigger and trigger_path is not None and trigger_path.exists():
                try:
                    trigger_path.unlink()
                    _logger.info("[trigger] Consumed start trigger file: %s", trigger_path)
                except OSError:
                    pass

            time.sleep(poll_seconds)
            continue

        if not zips:
            if start_armed and consume_trigger and trigger_path is not None and trigger_path.exists():
                try:
                    trigger_path.unlink()
                    _logger.info("[trigger] Consumed start trigger file: %s", trigger_path)
                except OSError:
                    pass
            time.sleep(poll_seconds)
            continue

        for zip_path in zips:
            if running_jobs_count(state_dir / "running") >= max_parallel:
                break

            job_id = zip_path.stem
            failed_marker = state_dir / "failed" / job_id
            if failed_marker.exists() and not keep_failed_marker:
                failed_marker.unlink(missing_ok=True)
            if (state_dir / "done" / job_id).exists() or (state_dir / "failed" / job_id).exists():
                continue

            claim_id = claim_job(state_dir / "queue", state_dir / "running", zip_path)
            if claim_id is not None:
                _logger.info("[claim] Claimed job %s", claim_id)
                if persistent_armed and persistent_trigger_path is not None:
                    mark_persistent_trigger_handled(persistent_trigger_path, state_dir)
                claim_path = state_dir / "queue" / f"{claim_id}.claimed"
                try:
                    success = start_job(
                        script_dir,
                        nc_output_dir,
                        task_prompt_file,
                        state_dir,
                        claim_id,
                        zip_path,
                        work_dir=work_dir,
                        log_dir=log_dir,
                        nc_log_dir=nc_log_dir,
                        log_sync_seconds=log_sync_seconds,
                        keep_work_dir=keep_work_dir,
                        post_sync_hook_cmd=post_sync_hook_cmd,
                        post_sync_hook_timeout_seconds=post_sync_hook_timeout_seconds,
                        keep_failed_marker=keep_failed_marker,
                    )
                finally:
                    claim_path.unlink(missing_ok=True)
                if (not success) and stop_loop_on_job_failure:
                    consume_start_trigger_if_needed(trigger_path, consume_trigger)
                    _logger.error(
                        "[loop] stopping due to job failure (STOP_LOOP_ON_JOB_FAILURE=1, job=%s)",
                        claim_id,
                    )
                    raise SystemExit(1)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
