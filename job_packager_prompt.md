# Create a Ralph Job Zip For night_worker_ai

You are preparing an input job for the Ralph loop in `night_worker_ai`.

## Goal
Given my requirements below, produce a single zip file named `<job_id>.zip` that can be dropped into `INPUT_DIR` for the loop.

## Output contract (must follow exactly)
- Produce: `<job_id>.zip`
- Zip root must contain:
  - `PRD.md` (the task list / plan, clearly chunked into atomic tasks)
  - `progress.txt` (create if missing; start empty or with a short header)
  - Optional: any starter project files (source code, package manifests, config, etc.)
- Also include (recommended):
  - `next_instruction.txt` (create if missing; can be empty; it will be overwritten by the loop)

Do not include any absolute paths. Do not nest everything under a top-level folder unless explicitly asked.

## PRD format rules
- Use Markdown checklist items (`- [ ] ...`) so tasks can be marked done.
- Tasks must be small and independently completable (one task per iteration).
- Put tasks in priority order.
- Include a "Definition of Done" section.

## progress.txt rules
- Start with:
  - `# Progress Log`
- Leave it mostly empty; the loop will append to it.

## My requirements (fill in)
- job_id: <INSERT_JOB_ID>
- Project description:
  <INSERT_DESCRIPTION>
- Constraints:
  <INSERT_CONSTRAINTS>
- Tech stack / language:
  <INSERT_STACK>
- Repo/project starter needed?
  - If yes, include minimal scaffolding.
  - If no, only include PRD.md + progress.txt (+ next_instruction.txt).

## Deliverable
Return:
1. A brief description of what files you put in the zip.
2. The exact file tree that will be zipped.
3. The command(s) you ran to create `<job_id>.zip` (or the steps to do so).

