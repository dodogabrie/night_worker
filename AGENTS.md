# Repository Guidelines

## Project Structure & Module Organization

- `ralph_loop.py`: host-side orchestrator that polls `INPUT_DIR` for `*.zip` jobs and runs the worker container.
- `ralph-loop.sh`: thin launcher for `ralph_loop.py`.
- `docker-compose.yml`: defines the `worker` service (read-only container; writable `tmpfs:/tmp` and `claude_home` volume).
- `worker/worker.py`: in-container job runner (unzips input, runs iterations, zips results).
- `worker/Dockerfile`: Alpine image with Python + `@anthropic-ai/claude-code`.
- `.env.example`: configuration template for local runs.

## Build, Test, and Development Commands

```bash
cp .env.example .env
docker compose build worker
docker compose run --rm --entrypoint claude worker setup-token
./ralph-loop.sh
```

- `docker compose build worker`: builds the local worker image (`night-worker:local`).
- `docker compose run ... setup-token`: performs one-time Claude CLI auth; data persists in the `claude_home` volume.
- `./ralph-loop.sh`: starts the polling loop (reads `.env` unless `ENV_FILE` is set).

## Coding Style & Naming Conventions

- Python: follow PEP 8, 4-space indentation, type hints where they improve clarity.
- Paths and env vars: keep names explicit and consistent with `.env.example` (e.g., `MAX_ITERATIONS`, `COMPLETE_SIGNAL`).
- Artifacts: output files follow `<job_id>.result.zip` and `<job_id>.status`.

## Testing Guidelines

- No dedicated test suite is currently included. Validate changes by running a small end-to-end job:
- Place `INPUT_DIR/<job_id>.zip` and confirm `OUTPUT_DIR/<job_id>.result.zip` plus `OUTPUT_DIR/<job_id>.status` are produced.
- When changing stop/timeout logic, inspect per-iteration logs under `logs/` inside the result archive.

## Commit & Pull Request Guidelines

- Git history is not available in this workspace; use a consistent convention such as Conventional Commits:
- Examples: `feat: add transient error backoff`, `fix: handle missing INPUT_DIR`.
- PRs should include: what changed, why, how to run locally, and any `.env`/contract changes (update `README.md` if behavior changes).

## Security & Configuration Notes

- The worker runs `read_only: true`; only `/tmp` and `/home/worker` are writable. If you need additional write paths, adjust `docker-compose.yml` deliberately.
- Secrets/tokens should not be committed; keep them in `.env` and the `claude_home` Docker volume.
