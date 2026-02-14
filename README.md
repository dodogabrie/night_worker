# Night Worker Ralph Loop

This repository runs a Dockerized batch code-worker loop that:

1. Polls a Nextcloud input folder for `.zip` projects.
2. Copies input to a local work directory.
3. Processes each project in an isolated worker container (local dirs only).
4. Syncs iteration logs and output to Nextcloud live during the run.
5. Stops safely on detected rate-limit or time budget.
6. Re-zips output and copies it to a Nextcloud output folder.
7. Stops early when the assistant prints a completion signal (`RALPH_COMPLETE` by default).

## Architecture

```
ralph_loop.py → copy input from Nextcloud to local work dir
             → docker run → container writes to LOCAL dirs only
             → poll loop: sync logs to Nextcloud (live)
             → on exit: copy output to Nextcloud
```

The container never touches Nextcloud directories directly. The host orchestrator
handles all Nextcloud I/O since it runs as `edoardo:www-data`.

## Files

- `ralph_loop.py`: host orchestrator loop (Python).
- `ralph-loop.sh`: thin launcher for `ralph_loop.py`.
- `docker-compose.yml`: worker service definition.
- `worker/worker.py`: in-container job execution logic (Python).
- `worker/Dockerfile`: worker image with Claude Code CLI installed.
- `.env.example`: runtime configuration template.

## Setup

1. Create config:

```bash
cp .env.example .env
```

2. Edit `.env` and set:
- `INPUT_DIR` and `OUTPUT_DIR` to Nextcloud-synced folders (host reads/writes these).
- `WORK_DIR` to a local directory for ephemeral job data.
- `LOG_DIR` to where system and per-job logs are written.
- `NEXTCLOUD_LOG_DIR` to where live iteration logs are synced for mobile monitoring.
- `CLAUDE_CMD` / `CLAUDE_ARGS` if you need a non-default Claude invocation.
- `CLAUDE_INPUT_MODE`:
  - `stdin` for CLIs that read prompt from stdin.
  - `arg` for CLIs that support `--prompt ...`.

3. Build the worker image:

```bash
docker compose build worker
```

4. One-time authentication token setup (persisted in Docker volume `claude_home`):

```bash
docker compose run --rm --entrypoint claude worker setup-token
```

5. Start loop:

```bash
./ralph-loop.sh
```

## Logs

| Log | Location | Content |
|-----|----------|---------|
| System log | `LOG_DIR/night-worker.log` | All stages: triggers, scans, claims, docker, sync, errors |
| Job log | `LOG_DIR/jobs/<job_id>.log` | Per-job lifecycle detail |
| Nextcloud log | `NEXTCLOUD_LOG_DIR/<job_id>.log` | Combined Claude iteration output (updates live) |
| Nextcloud status | `NEXTCLOUD_LOG_DIR/<job_id>.status` | One-liner: running/done/failed + progress |

## Nextcloud file visibility (auto-scan)

If files are written directly into Nextcloud data folders, web/mobile may not show them
until Nextcloud file cache is refreshed. You can automate this at the end of each job:

- Set `POST_SYNC_HOOK_CMD` in `.env` to run `occ files:scan`
- Optional timeout: `POST_SYNC_HOOK_TIMEOUT_SECONDS` (default `180`)

Hook environment variables:
- `JOB_ID`
- `NC_OUTPUT_DIR`
- `NC_LOG_DIR`

Example (host-installed Nextcloud):

```bash
POST_SYNC_HOOK_CMD='sudo -n -u www-data php /var/www/nextcloud/occ files:scan --path="edoardo/files/RalphLoop/out" && sudo -n -u www-data php /var/www/nextcloud/occ files:scan --path="edoardo/files/RalphLoop/logs"'
```

Note: this requires the orchestrator runtime to be able to run `occ` without an interactive password.
If orchestrator runs in Docker, host `sudo/occ` is not directly available inside that container.
In that case either run orchestrator on host, or use a host cron/systemd timer for scanning.

## Triggering runs

Option A: `start.md` trigger file

If you want the loop to sit idle until a file appears (e.g. drop `start.md` into your jobs folder), set:
- `START_TRIGGER_DIR` to the host path of your jobs folder
- `START_TRIGGER_FILE=start.md`

When `CONSUME_TRIGGER=1` (default), the loop deletes the start trigger file after the run finishes.

Option B: cron at 1am

If you want a nightly schedule that doesn't require deleting the trigger file, use the persistent trigger:
- Set `PERSISTENT_TRIGGER_DIR` and `PERSISTENT_TRIGGER_FILE` (e.g. `night.md`)
- Cron should `touch` that file at 1am; the loop runs only when the file's mtime increases.

```cron
0 1 * * * /usr/bin/touch /mnt/nextcloud-data/edoardo/files/RalphLoop/in/night.md
```

## Strict Single-Zip Contract (optional)

If you want `INPUT_DIR` to contain exactly one zip (the "current" project state) and have the loop resume from the
latest version in `OUTPUT_DIR`, enable:
- `STRICT_SINGLE_ZIP_CONTRACT=1`

Rules:
- If `INPUT_DIR` contains 0 zips: loop idles.
- If `INPUT_DIR` contains more than 1 zip: the loop aborts (contract violation).
- The loop will start from the latest `OUTPUT_DIR/<job_id>_vN.zip` if present; otherwise it starts from `INPUT_DIR/<job_id>.zip`.
- To avoid confusing states, versioned zips in `INPUT_DIR` (containing `_v`) are rejected unless `STRICT_ALLOW_VERSIONED_INPUTS=1`.

## Input/Output contract

- Input job: `INPUT_DIR/<job_id>.zip`
- Output archive: `OUTPUT_DIR/<job_id>.result.zip` (or `<job_id>_vN.zip` in zip-chain mode)
- Output status marker: `OUTPUT_DIR/<job_id>.status`

## Zip-chain mode (optional)

If you want each iteration to be stateless and use the previous iteration archive as its only input, enable:
- `ZIP_CHAIN_MODE=1`

In this mode the worker emits per-iteration archives:
- `OUTPUT_DIR/<job_id>_v1.zip`, `OUTPUT_DIR/<job_id>_v2.zip`, ...
- plus per-iteration status files: `OUTPUT_DIR/<job_id>_vN.status`

The worker also asks the assistant to update `NEXT_INSTRUCTION_FILE` (default `next_instruction.txt`) inside the project each iteration, and includes it in the next iteration prompt (no chat context assumed).

Environment knobs:
- `PRD_FILE` (default `PRD.md`)
- `PROGRESS_FILE` (default `progress.txt`)

Inside each result zip:
- `project/` (final project state)
- `logs/` (iteration logs)
- `metadata.txt` (status and timestamps)

## Work dir cleanup

After a job completes, the local work directory (`WORK_DIR/<job_id>/`) is cleaned up
based on the `KEEP_WORK_DIR` setting:
- `on_failure` (default): keep on failure for debugging, delete on success
- `always`: always keep
- `never`: always delete

## Failure behavior controls

You can tune what happens after a failed job:

- `KEEP_FAILED_MARKER` (default `1`):
  - `1`: write `.state/failed/<job_id>` and skip that job until marker is removed.
  - `0`: do not keep failed marker (no manual `rm` needed for future retries).
- `STOP_LOOP_ON_JOB_FAILURE` (default `0`):
  - `1`: exit the loop process immediately after a job failure.
  - useful for "fail-fast and retry later" workflows.

Also, queue claim symlinks are now cleaned automatically after each run.

## Ralph convention inside each project zip

For best results include:
- `prd.json`: backlog with tasks and completion flags
- `progress.txt`: iteration-by-iteration memory log

The worker prompt enforces:
- one task per iteration
- `progress.txt` update each iteration
- print `RALPH_COMPLETE` (or your `COMPLETE_SIGNAL`) when all tasks are done

## Notes

- The worker container runs as `1000:1000` and is `read_only`; only `/tmp`, `/job`, and `/home/worker` (named volume) are writable.
- The container only accesses local directories bind-mounted by the host — never Nextcloud directly.
- Claude auth/token data is persisted in `/home/worker` via the `claude_home` volume.
- Rate-limit detection is heuristic (`429`, `rate limit`, `retry after` in logs) and can be tuned in `worker/worker.py`.
