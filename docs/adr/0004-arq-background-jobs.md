# 0004. ARQ for background jobs

- Status: accepted
- Date: 2026-10-12

## Context

Several operations should not run inside a request: sending email (slow, can fail), building weekly
reports for every user, and housekeeping (expired tokens, demo accounts). The stack already includes
Redis for rate limiting and caching, and the code base is async end to end.

Options considered:

- **FastAPI `BackgroundTasks`**: runs in the API process after the response; no retries, no scheduling,
  lost on restart.
- **Celery**: mature, but sync-first, a heavier dependency surface, and asyncio support relies on
  workarounds.
- **Dramatiq / RQ**: sync-first as well.
- **ARQ**: small asyncio-native queue on Redis with retries, timeouts, job results and cron scheduling.

## Decision

Use ARQ. Jobs live in `app/worker/tasks.py`, receive shared resources (session factory, Redis, email
sender) through the worker context and are registered in `WorkerSettings` along with three crons.

The API depends on a small `JobQueue` protocol rather than ARQ directly. `ArqJobQueue` enqueues to
Redis; `EagerJobQueue` runs the same functions in-process when `JOBS_EAGER=true`, which the test suite
uses (so email flows are tested end to end) and which suits single-container deployments.

## Consequences

- One more process to run (`arq app.worker.settings.WorkerSettings`), with its own healthcheck
  (`arq --check`).
- ARQ is in maintenance mode and caps the `redis` client below 6.0, so the project pins `redis` 5.x.
  The Redis server itself can be any recent version (the stack runs Redis 8). If ARQ becomes a
  constraint, the `JobQueue` protocol keeps a migration (for example to a Redis Streams based queue)
  contained to `app/worker`.
- Jobs must be idempotent or guarded: crons may fire more than once around deploys, so weekly
  dispatch uses a per-user, per-week `SET NX` key.
- Cron schedules are evaluated per worker; production should run crons in a single worker process
  (or keep one worker deployment) to avoid duplicate scheduling work.
