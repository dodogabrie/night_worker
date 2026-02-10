# Night Worker Ralph Loop

This repository runs a Dockerized batch code-worker loop that:

1. Polls a Nextcloud input folder for `.zip` projects.
2. Processes each project in an isolated worker container.
3. Stops safely on detected rate-limit or time budget.
4. Re-zips output and writes it to a Nextcloud output folder.
5. Stops early when the assistant prints a completion signal (`RALPH_COMPLETE` by default).

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
- `INPUT_DIR` and `OUTPUT_DIR` to local Nextcloud-synced folders.
- `CLAUDE_CMD` / `CLAUDE_ARGS` if you need a non-default Claude invocation.
- `CLAUDE_INPUT_MODE`:
  - `stdin` for CLIs that read prompt from stdin.
  - `arg` for CLIs that support `--prompt ...`.
- Optional: if you want the worker to write outputs into the `/jobs` mount (from `JOBS_VOLUME`),
  set `WORKER_OUTPUT_DIR=/jobs` and make `JOBS_VOLUME` writable (omit `:ro`). In this case,
  artifacts are written under `/jobs` (the `JOBS_VOLUME` mount), not the host `OUTPUT_DIR`.

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
0 1 * * * /usr/bin/touch /mnt/nextcloud-data/edoardo/ralph_loop/in/night.md
```

If you do not run the loop all day, cron can start it, but note `ralph_loop.py` runs forever:

```cron
0 1 * * * cd /home/edoardo/Documents/Personale/night_worker_ai && /usr/bin/timeout 6h ./ralph-loop.sh
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

Recommended layout on a single shared jobs volume:
- Use two subfolders inside the same mounted volume to keep inputs clean while still persisting all versions:
  - `INPUT_DIR=/jobs/in` (contains exactly one starting zip in strict mode)
  - `OUTPUT_DIR=/jobs/out` (accumulates `*_vN.zip` and `*.status`)
  - `/jobs/logs` (optional; a single “last run” log file for quick monitoring)

This also lets you mount inputs read-only and outputs read-write (recommended):
- Mount `/jobs/in` as read-only and `/jobs/out` as read-write (two separate bind mounts or volumes).
- Keep the worker writing to the output mount (`WORKER_OUTPUT_DIR=/jobs/out`).
 - If you mount `/jobs/logs` read-write and set `EXTERNAL_LOG_DIR=/jobs/logs`, the worker will overwrite `logs/<job_id>.last.log` each iteration.

## Input/Output contract

- Input job: `INPUT_DIR/<job_id>.zip`
- Output archive: `OUTPUT_DIR/<job_id>.result.zip`
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

## Ralph convention inside each project zip

For best results include:
- `prd.json`: backlog with tasks and completion flags
- `progress.txt`: iteration-by-iteration memory log

The worker prompt enforces:
- one task per iteration
- `progress.txt` update each iteration
- print `RALPH_COMPLETE` (or your `COMPLETE_SIGNAL`) when all tasks are done

## Notes

- The worker container is `read_only`; only `/tmp` and `/home/worker` (named volume) are writable.
- Claude auth/token data is persisted in `/home/worker` via the `claude_home` volume.
- Rate-limit detection is heuristic (`429`, `rate limit`, `retry after` in logs) and can be tuned in `worker/worker.py`.
