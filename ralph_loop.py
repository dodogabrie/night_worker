#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def log(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {message}", flush=True)


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
    log(f"Created default task prompt at {task_prompt_file}")


def claim_job(state_queue: Path, zip_path: Path) -> str | None:
    job_id = zip_path.stem
    claim_path = state_queue / f"{job_id}.claimed"
    try:
        os.symlink(str(zip_path), str(claim_path))
        return job_id
    except FileExistsError:
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


def start_job(
    script_dir: Path,
    output_dir: Path,
    task_prompt_file: Path,
    state_dir: Path,
    job_id: str,
    zip_path: Path,
    *,
    version_offset: int = 0,
) -> None:
    running_marker = state_dir / "running" / job_id
    done_marker = state_dir / "done" / job_id
    failed_marker = state_dir / "failed" / job_id
    tmp_root = Path(tempfile.mkdtemp())

    log(f"Starting job {job_id}")
    running_marker.touch()

    env = os.environ.copy()
    # By default, the worker writes outputs to /job/output (bind-mounted from OUTPUT_DIR).
    # If you mount a shared jobs volume (e.g. /jobs) and want the worker to write there,
    # set WORKER_OUTPUT_DIR=/jobs and ensure the /jobs mount is writable.
    worker_output_dir = env.get("WORKER_OUTPUT_DIR", "/job/output")
    command = [
        "docker",
        "compose",
        "-f",
        str(script_dir / "docker-compose.yml"),
        "run",
        "--rm",
        "-e",
        f"JOB_ID={job_id}",
        "-e",
        "INPUT_ZIP=/job/input.zip",
        "-e",
        f"OUTPUT_DIR={worker_output_dir}",
        "-e",
        "TASK_PROMPT_FILE=/job/task_prompt.txt",
        "-e",
        f"MAX_ITERATIONS={env.get('MAX_ITERATIONS', '8')}",
        "-e",
        f"MAX_SECONDS={env.get('MAX_SECONDS', '3600')}",
        "-e",
        f"ITER_TIMEOUT_SECONDS={env.get('ITER_TIMEOUT_SECONDS', '600')}",
        "-e",
        f"SOFT_STOP_MARGIN_SECONDS={env.get('SOFT_STOP_MARGIN_SECONDS', '90')}",
        "-e",
        f"CLAUDE_CMD={env.get('CLAUDE_CMD', 'claude')}",
        "-e",
        f"CLAUDE_ARGS={env.get('CLAUDE_ARGS', '--print')}",
        "-e",
        f"CLAUDE_INPUT_MODE={env.get('CLAUDE_INPUT_MODE', 'stdin')}",
        "-e",
        f"COMPLETE_SIGNAL={env.get('COMPLETE_SIGNAL', 'RALPH_COMPLETE')}",
        "-e",
        f"MAX_CONSECUTIVE_TRANSIENT_ERRORS={env.get('MAX_CONSECUTIVE_TRANSIENT_ERRORS', '4')}",
        "-e",
        f"TRANSIENT_BACKOFF_SECONDS={env.get('TRANSIENT_BACKOFF_SECONDS', '20')}",
        "-e",
        f"ZIP_CHAIN_MODE={env.get('ZIP_CHAIN_MODE', '0')}",
        "-e",
        f"NEXT_INSTRUCTION_FILE={env.get('NEXT_INSTRUCTION_FILE', 'next_instruction.txt')}",
        "-e",
        f"VERSION_OFFSET={version_offset}",
        "-e",
        f"EXTERNAL_LOG_DIR={env.get('EXTERNAL_LOG_DIR', '')}",
        "-v",
        f"{zip_path}:/job/input.zip:ro",
        "-v",
        f"{task_prompt_file}:/job/task_prompt.txt:ro",
        "-v",
        f"{tmp_root}:/tmp/work",
        "worker",
    ]

    # Only mount OUTPUT_DIR to /job/output when the worker is using that default path.
    # If WORKER_OUTPUT_DIR points elsewhere (e.g. /jobs), we rely on docker-compose.yml
    # volumes (e.g. JOBS_VOLUME) to provide the right writable mount.
    if worker_output_dir == "/job/output":
        command.extend(["-v", f"{output_dir}:/job/output"])

    rc = subprocess.run(command, env=env).returncode

    shutil.rmtree(tmp_root, ignore_errors=True)
    running_marker.unlink(missing_ok=True)

    if rc == 0:
        done_marker.touch()
        log(f"Job {job_id} completed")
    else:
        failed_marker.touch()
        log(f"Job {job_id} failed (rc={rc})")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    env_file = Path(os.environ.get("ENV_FILE", str(script_dir / ".env")))
    load_env_file(env_file)

    input_dir = Path(os.environ.get("INPUT_DIR", "/srv/nextcloud/night_worker/input"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/srv/nextcloud/night_worker/output"))
    state_dir = Path(os.environ.get("STATE_DIR", str(script_dir / ".state")))
    task_prompt_file = Path(os.environ.get("TASK_PROMPT_FILE", str(script_dir / "task_prompt.txt")))
    poll_seconds = env_int("POLL_SECONDS", 20)
    max_parallel = env_int("MAX_PARALLEL", 1)
    consume_trigger = env_bool("CONSUME_TRIGGER", True)
    trigger_path = resolve_trigger_path(script_dir)
    persistent_trigger_path = resolve_persistent_trigger_path(script_dir)
    strict_single_zip = env_bool("STRICT_SINGLE_ZIP_CONTRACT", False)
    strict_allow_versioned_inputs = env_bool("STRICT_ALLOW_VERSIONED_INPUTS", False)

    for path in [
        input_dir,
        output_dir,
        state_dir / "queue",
        state_dir / "running",
        state_dir / "done",
        state_dir / "failed",
        state_dir / "trigger",
    ]:
        path.mkdir(parents=True, exist_ok=True)

    ensure_default_prompt(task_prompt_file)
    if trigger_path is not None or persistent_trigger_path is not None:
        log(
            "Ralph loop started (trigger-gated). "
            f"Start trigger: {trigger_path or 'disabled'} "
            f"Persistent trigger: {persistent_trigger_path or 'disabled'} "
            f"Input: {input_dir} Output: {output_dir}"
        )
    else:
        log(f"Ralph loop started. Input: {input_dir} Output: {output_dir}")

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

            # Continue from latest <job_id>_vN.zip if present; otherwise start from base zip.
            latest_zip, latest_n = find_latest_version_zip(output_dir, job_id)
            input_zip_for_run = latest_zip or zip_path
            version_offset = latest_n

            if running_jobs_count(state_dir / "running") < max_parallel:
                if persistent_armed and persistent_trigger_path is not None:
                    mark_persistent_trigger_handled(persistent_trigger_path, state_dir)
                start_job(
                    script_dir,
                    output_dir,
                    task_prompt_file,
                    state_dir,
                    job_id,
                    input_zip_for_run,
                    version_offset=version_offset,
                )

            # In strict mode, we treat "one zip" as one unit of work; consume the start trigger after the run ends.
            if start_armed and consume_trigger and trigger_path is not None and trigger_path.exists():
                try:
                    trigger_path.unlink()
                    log(f"Consumed start trigger file: {trigger_path}")
                except OSError:
                    pass

            time.sleep(poll_seconds)
            continue

        if not zips:
            # Drain behavior: if trigger gating is enabled, optionally consume the trigger
            # once there is no work left.
            if start_armed and consume_trigger and trigger_path is not None and trigger_path.exists():
                try:
                    trigger_path.unlink()
                    log(f"Consumed start trigger file: {trigger_path}")
                except OSError:
                    # Don't crash the loop on filesystem quirks.
                    pass
            time.sleep(poll_seconds)
            continue

        for zip_path in zips:
            if running_jobs_count(state_dir / "running") >= max_parallel:
                break

            job_id = zip_path.stem
            if (state_dir / "done" / job_id).exists() or (state_dir / "failed" / job_id).exists():
                continue

            claim_id = claim_job(state_dir / "queue", zip_path)
            if claim_id is not None:
                if persistent_armed and persistent_trigger_path is not None:
                    mark_persistent_trigger_handled(persistent_trigger_path, state_dir)
                start_job(script_dir, output_dir, task_prompt_file, state_dir, claim_id, zip_path)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
