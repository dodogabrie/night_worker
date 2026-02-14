#!/usr/bin/env python3
import os
import re
import shlex
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


RATE_LIMIT_RE = re.compile(r"rate.?limit|429|too many requests|retry after|quota exceeded", re.IGNORECASE)
TRANSIENT_RE = re.compile(
    r"status code 502|status code 503|status code 504|bad gateway|gateway timeout|service unavailable|temporarily unavailable|upstream",
    re.IGNORECASE,
)
CONTEXT_RE = re.compile(
    r"context length|maximum context|prompt too long|input too long|too many tokens|token limit|context window",
    re.IGNORECASE,
)


def log(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {message}", flush=True)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required env: {name}")
    return value


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


def detect(pattern: re.Pattern[str], log_file: Path) -> bool:
    if not log_file.exists():
        return False
    return bool(pattern.search(log_file.read_text(encoding="utf-8", errors="ignore")))


def write_external_last_log(
    external_log_dir: Path,
    job_id: str,
    iteration_label: str,
    status: str,
    stop_reason: str,
    iter_log: Path,
) -> None:
    external_log_dir.mkdir(parents=True, exist_ok=True)
    out_path = external_log_dir / f"{job_id}.last.log"
    header = "\n".join(
        [
            f"job_id={job_id}",
            f"iteration={iteration_label}",
            f"status={status}",
            f"stop_reason={stop_reason or 'none'}",
            f"updated_at_unix={int(time.time())}",
            "",
        ]
    )
    body = ""
    if iter_log.exists():
        body = iter_log.read_text(encoding="utf-8", errors="ignore")
    out_path.write_text(header + body, encoding="utf-8")


def copy_tree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def zip_dir(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))


def extract_job_zip(input_zip: Path, project_dir: Path, scratch_dir: Path) -> None:
    """
    Extract a job zip into project_dir.

    Supports both:
    - input zips that contain project files at the archive root
    - result zips produced by this worker that contain a top-level "project/" folder
    """
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(input_zip, "r") as zf:
        zf.extractall(scratch_dir)

    nested_project = scratch_dir / "project"
    src = nested_project if nested_project.is_dir() else scratch_dir
    copy_tree_contents(src, project_dir)


def write_result_archive(
    job_id: str,
    output_dir: Path,
    result_staging: Path,
    project_dir: Path,
    log_dir: Path,
    start_ts: int,
    attempted: int,
    status: str,
    stop_reason: str,
    name_suffix: str,
) -> Path:
    if result_staging.exists():
        shutil.rmtree(result_staging)
    result_staging.mkdir(parents=True, exist_ok=True)

    metadata = result_staging / "metadata.txt"
    metadata.write_text(
        "\n".join(
            [
                f"job_id={job_id}",
                f"status={status}",
                f"stop_reason={stop_reason or 'none'}",
                f"started_at_unix={start_ts}",
                f"ended_at_unix={int(time.time())}",
                f"iterations_attempted={attempted}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    project_stage = result_staging / "project"
    logs_stage = result_staging / "logs"
    copy_tree_contents(project_dir, project_stage)
    copy_tree_contents(log_dir, logs_stage)

    archive_tmp = output_dir / f"{job_id}{name_suffix}.partial.zip"
    archive_final = output_dir / f"{job_id}{name_suffix}.zip"

    log(f"[{job_id}] writing result archive {archive_final.name}")
    zip_dir(result_staging, archive_tmp)
    archive_tmp.replace(archive_final)
    return archive_final


def run_iteration(
    job_id: str,
    iteration: int,
    log_dir: Path,
    project_dir: Path,
    prompt_text: str,
    claude_cmd: str,
    claude_args: str,
    claude_input_mode: str,
    iter_timeout_seconds: int,
) -> int:
    iter_log = log_dir / f"iter-{iteration}.log"
    log(f"[{job_id}] iteration {iteration} starting")

    cmd = [claude_cmd, *shlex.split(claude_args)]
    if claude_input_mode != "stdin":
        cmd.extend(["-p", prompt_text])

    try:
        with iter_log.open("w", encoding="utf-8") as f:
            if claude_input_mode == "stdin":
                completed = subprocess.run(
                    cmd,
                    input=prompt_text,
                    text=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=iter_timeout_seconds,
                    cwd=str(project_dir),
                    check=False,
                )
            else:
                completed = subprocess.run(
                    cmd,
                    text=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=iter_timeout_seconds,
                    cwd=str(project_dir),
                    check=False,
                )
    except subprocess.TimeoutExpired:
        log(f"[{job_id}] iteration {iteration} timed out")
        return 124

    return completed.returncode


def main() -> None:
    job_id = require_env("JOB_ID")
    input_zip = Path(require_env("INPUT_ZIP"))
    output_dir = Path(require_env("OUTPUT_DIR"))
    task_prompt_file = Path(require_env("TASK_PROMPT_FILE"))

    work_root = Path(os.environ.get("WORK_ROOT", "/tmp/work"))
    project_dir = work_root / "project"
    log_dir = work_root / "logs"
    result_staging = work_root / "result"
    scratch_dir = work_root / "extract"

    max_iterations = env_int("MAX_ITERATIONS", 8)
    max_seconds = env_int("MAX_SECONDS", 3600)
    iter_timeout_seconds = env_int("ITER_TIMEOUT_SECONDS", 600)
    soft_stop_margin_seconds = env_int("SOFT_STOP_MARGIN_SECONDS", 90)
    claude_cmd = os.environ.get("CLAUDE_CMD", "claude")
    claude_args = os.environ.get("CLAUDE_ARGS", "--print")
    claude_input_mode = os.environ.get("CLAUDE_INPUT_MODE", "stdin")
    complete_signal = os.environ.get("COMPLETE_SIGNAL", "RALPH_COMPLETE")
    max_consecutive_transient_errors = env_int("MAX_CONSECUTIVE_TRANSIENT_ERRORS", 4)
    transient_backoff_seconds = env_int("TRANSIENT_BACKOFF_SECONDS", 20)
    zip_chain_mode = env_bool("ZIP_CHAIN_MODE", False)
    next_instruction_file_name = os.environ.get("NEXT_INSTRUCTION_FILE", "next_instruction.txt")
    prd_file_name = os.environ.get("PRD_FILE", "PRD.md")
    progress_file_name = os.environ.get("PROGRESS_FILE", "progress.txt")
    version_offset = env_int("VERSION_OFFSET", 0)
    external_log_dir_raw = os.environ.get("EXTERNAL_LOG_DIR", "").strip()
    external_log_dir = Path(external_log_dir_raw) if external_log_dir_raw else None

    project_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    result_staging.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    if not input_zip.is_file():
        raise SystemExit(f"Input zip not found: {input_zip}")
    if not task_prompt_file.is_file():
        raise SystemExit(f"Task prompt file not found: {task_prompt_file}")

    start_ts = int(time.time())
    status = "done"
    stop_reason = ""

    if zip_chain_mode:
        task_prompt = task_prompt_file.read_text(encoding="utf-8")
        current_zip = input_zip
        iteration = 1
        attempted = 0
        consecutive_transient_errors = 0

        log(f"[{job_id}] zip-chain mode enabled")

        while iteration <= max_iterations:
            remaining = max_seconds - (int(time.time()) - start_ts)
            if remaining <= soft_stop_margin_seconds:
                status = "stopped_rate_limit"
                stop_reason = "soft_budget_guard"
                log(f"[{job_id}] soft stop due to global time budget")
                break

            log(f"[{job_id}] unpacking zip for iteration {iteration}: {current_zip.name}")
            extract_job_zip(current_zip, project_dir, scratch_dir)

            progress_file = project_dir / progress_file_name
            prd_file = project_dir / prd_file_name
            next_instruction_file = project_dir / next_instruction_file_name

            if not progress_file.exists():
                progress_file.write_text("# Progress Log\n\n", encoding="utf-8")
            if not prd_file.exists():
                prd_file.write_text("# PRD\n\n- [ ] Define tasks\n", encoding="utf-8")

            handoff_text = ""
            if next_instruction_file.exists():
                handoff_text = next_instruction_file.read_text(encoding="utf-8", errors="ignore").strip()

            prompt_text = (
                f"@{prd_file_name} @{progress_file_name}\n\n"
                f"{task_prompt}\n\n"
                f"Project path: {project_dir}\n"
                "Ralph rules:\n"
                "1) Read the PRD and progress file\n"
                "2) Find the next incomplete/highest-priority task and implement it\n"
                "3) Run tests/typechecks/linters if present\n"
                f"4) Commit your changes\n"
                f"5) Append your progress to {progress_file_name}\n"
                f"6) Before finishing, update {next_instruction_file_name} with a self-contained instruction for the next iteration (assume no chat context)\n"
                f"7) ONLY DO ONE TASK AT A TIME\n"
                f"8) If the PRD is complete, output {complete_signal}\n"
            )
            if handoff_text:
                prompt_text += "\nPrevious iteration handoff:\n" + handoff_text + "\n"

            attempted += 1
            rc = run_iteration(
                job_id,
                iteration,
                log_dir,
                project_dir,
                prompt_text,
                claude_cmd,
                claude_args,
                claude_input_mode,
                iter_timeout_seconds,
            )

            iter_log = log_dir / f"iter-{iteration}.log"

            iter_status = "in_progress"
            iter_stop_reason = ""
            hard_stop = False

            if rc != 0 and detect(RATE_LIMIT_RE, iter_log):
                iter_status = "stopped_rate_limit"
                iter_stop_reason = "rate_limit_detected"
                status = iter_status
                stop_reason = iter_stop_reason
                hard_stop = True
                log(f"[{job_id}] hard stop due to rate-limit signal")
            elif rc != 0 and detect(CONTEXT_RE, iter_log):
                iter_status = "stopped_context_limit"
                iter_stop_reason = "context_limit_detected"
                status = iter_status
                stop_reason = iter_stop_reason
                hard_stop = True
                log(f"[{job_id}] hard stop due to context-limit signal")
            elif rc != 0 and detect(TRANSIENT_RE, iter_log):
                consecutive_transient_errors += 1
                log(
                    f"[{job_id}] transient upstream error detected "
                    f"({consecutive_transient_errors}/{max_consecutive_transient_errors})"
                )
                if consecutive_transient_errors >= max_consecutive_transient_errors:
                    iter_status = "failed"
                    iter_stop_reason = "too_many_transient_errors"
                    status = iter_status
                    stop_reason = iter_stop_reason
                    hard_stop = True
                    log(f"[{job_id}] failing after repeated transient errors")
            elif rc != 0 and rc != 124:
                iter_status = "failed"
                iter_stop_reason = f"assistant_exit_{rc}"
                status = iter_status
                stop_reason = iter_stop_reason
                hard_stop = True
                log(f"[{job_id}] worker failed with rc={rc}")
            elif complete_signal in iter_log.read_text(encoding="utf-8", errors="ignore"):
                iter_status = "done"
                iter_stop_reason = "complete_signal"
                status = iter_status
                stop_reason = iter_stop_reason
                hard_stop = True
                log(f"[{job_id}] completion signal detected")
            else:
                consecutive_transient_errors = 0

            # If we reached the last iteration without completion, mark cap stop here
            # so the last emitted zip reflects the final status.
            if not hard_stop and iteration == max_iterations:
                iter_status = "stopped_iteration_cap"
                iter_stop_reason = "max_iterations_reached"
                status = iter_status
                stop_reason = iter_stop_reason
                hard_stop = True

            name_suffix = f"_v{version_offset + iteration}"
            current_zip = write_result_archive(
                job_id=job_id,
                output_dir=output_dir,
                result_staging=result_staging,
                project_dir=project_dir,
                log_dir=log_dir,
                start_ts=start_ts,
                attempted=attempted,
                status=iter_status,
                stop_reason=iter_stop_reason,
                name_suffix=name_suffix,
            )
            (output_dir / f"{job_id}{name_suffix}.status").write_text(f"{iter_status}\n", encoding="utf-8")

            if external_log_dir is not None:
                try:
                    write_external_last_log(
                        external_log_dir,
                        job_id,
                        str(version_offset + iteration),
                        iter_status,
                        iter_stop_reason,
                        iter_log,
                    )
                except OSError:
                    pass

            if hard_stop:
                break

            if rc != 0 and detect(TRANSIENT_RE, iter_log):
                time.sleep(transient_backoff_seconds)

            iteration += 1

        status_file = output_dir / f"{job_id}.status"
        status_file.write_text(f"{status}\n", encoding="utf-8")
        log(f"[{job_id}] completed with status={status}")
        return

    log(f"[{job_id}] unpacking input zip")
    with zipfile.ZipFile(input_zip, "r") as zf:
        zf.extractall(project_dir)

    progress_file = project_dir / progress_file_name
    prd_file = project_dir / prd_file_name

    if not progress_file.exists():
        progress_file.write_text("# Progress Log\n\n", encoding="utf-8")
    if not prd_file.exists():
        prd_file.write_text("# PRD\n\n- [ ] Define tasks\n", encoding="utf-8")

    task_prompt = task_prompt_file.read_text(encoding="utf-8")
    prompt_text = (
        f"@{prd_file_name} @{progress_file_name}\n\n"
        f"{task_prompt}\n\n"
        f"Project path: {project_dir}\n"
        "Ralph rules:\n"
        "1) Read the PRD and progress file\n"
        "2) Find the next incomplete/highest-priority task and implement it\n"
        "3) Run tests/typechecks/linters if present\n"
        "4) Commit your changes\n"
        f"5) Append your progress to {progress_file_name}\n"
        "6) ONLY DO ONE TASK AT A TIME\n"
        f"7) If the PRD is complete, output {complete_signal}\n"
    )

    prompt_file = work_root / "prompt.txt"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    iteration = 1
    attempted = 0
    consecutive_transient_errors = 0

    while iteration <= max_iterations:
        remaining = max_seconds - (int(time.time()) - start_ts)
        if remaining <= soft_stop_margin_seconds:
            status = "stopped_rate_limit"
            stop_reason = "soft_budget_guard"
            log(f"[{job_id}] soft stop due to global time budget")
            break

        attempted += 1
        rc = run_iteration(
            job_id,
            iteration,
            log_dir,
            project_dir,
            prompt_text,
            claude_cmd,
            claude_args,
            claude_input_mode,
            iter_timeout_seconds,
        )

        iter_log = log_dir / f"iter-{iteration}.log"
        if rc != 0:
            if detect(RATE_LIMIT_RE, iter_log):
                status = "stopped_rate_limit"
                stop_reason = "rate_limit_detected"
                log(f"[{job_id}] hard stop due to rate-limit signal")
                break

            if detect(CONTEXT_RE, iter_log):
                status = "stopped_context_limit"
                stop_reason = "context_limit_detected"
                log(f"[{job_id}] hard stop due to context-limit signal")
                break

            if detect(TRANSIENT_RE, iter_log):
                consecutive_transient_errors += 1
                log(
                    f"[{job_id}] transient upstream error detected "
                    f"({consecutive_transient_errors}/{max_consecutive_transient_errors})"
                )
                if consecutive_transient_errors >= max_consecutive_transient_errors:
                    status = "failed"
                    stop_reason = "too_many_transient_errors"
                    log(f"[{job_id}] failing after repeated transient errors")
                    break
                time.sleep(transient_backoff_seconds)
                iteration += 1
                continue

            if rc == 124:
                log(f"[{job_id}] continuing after iteration timeout")
                iteration += 1
                continue

            status = "failed"
            stop_reason = f"assistant_exit_{rc}"
            log(f"[{job_id}] worker failed with rc={rc}")
            break

        if complete_signal in iter_log.read_text(encoding="utf-8", errors="ignore"):
            status = "done"
            stop_reason = "complete_signal"
            log(f"[{job_id}] completion signal detected")
            break

        consecutive_transient_errors = 0
        iteration += 1

    if iteration > max_iterations and status == "done":
        status = "stopped_iteration_cap"
        stop_reason = "max_iterations_reached"

    summary_file = project_dir / "WORKER_SUMMARY.md"
    if not summary_file.exists():
        summary_file.write_text(
            "\n".join(
                [
                    "# Worker Summary",
                    "",
                    f"- job_id: {job_id}",
                    f"- status: {status}",
                    f"- stop_reason: {stop_reason or 'none'}",
                    f"- iterations_attempted: {attempted}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    metadata = result_staging / "metadata.txt"
    metadata.write_text(
        "\n".join(
            [
                f"job_id={job_id}",
                f"status={status}",
                f"stop_reason={stop_reason or 'none'}",
                f"started_at_unix={start_ts}",
                f"ended_at_unix={int(time.time())}",
                f"iterations_attempted={attempted}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    project_stage = result_staging / "project"
    logs_stage = result_staging / "logs"
    copy_tree_contents(project_dir, project_stage)
    copy_tree_contents(log_dir, logs_stage)

    archive_tmp = output_dir / f"{job_id}.result.partial.zip"
    archive_final = output_dir / f"{job_id}.result.zip"

    log(f"[{job_id}] writing result archive")
    zip_dir(result_staging, archive_tmp)
    archive_tmp.replace(archive_final)

    status_file = output_dir / f"{job_id}.status"
    status_file.write_text(f"{status}\n", encoding="utf-8")

    log(f"[{job_id}] completed with status={status}")


if __name__ == "__main__":
    main()
