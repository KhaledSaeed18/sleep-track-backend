# SleepTrack API

[![CI](https://github.com/KhaledSaeed18/sleep-track-backend/actions/workflows/ci.yml/badge.svg?branch=v2)](https://github.com/KhaledSaeed18/sleep-track-backend/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.143-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-8-DC382D?logo=redis&logoColor=white)
![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)
![mypy](https://img.shields.io/badge/mypy-strict-2A6DB2)
![Coverage](https://img.shields.io/badge/coverage-98%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

SleepTrack is a sleep journal with an analytics engine behind it. You log nights (by hand, with a
live timer, or by CSV import), tag them with things like caffeine, exercise or stress, and the API
turns that history into scores, trends, chronotype estimates, tag impact analysis, plain-language
insights and weekly email reports.

This repository is the backend: an async FastAPI service on PostgreSQL and Redis with a background
worker. It is built to production standards: layered modules, strict typing, RFC 9457 errors,
rotating refresh tokens with reuse detection, rate limiting, observability hooks and a test suite
that runs against real Postgres and Redis.

- **API docs:** `http://localhost:8000/api/v1/docs` (Swagger) and `/api/v1/redoc`
- **OpenAPI:** [`docs/openapi.json`](docs/openapi.json), also served at `/api/v1/openapi.json`
- **Frontend:** [sleep-track-frontend](https://github.com/KhaledSaeed18/sleep-track-frontend), a Next.js app that reaches this API through a same-origin proxy

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Testing and quality](#testing-and-quality)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Further reading](#further-reading)

## Features

**Accounts and security**
- Email/password registration with a password policy (length, letters + digits, common-password
  deny-list) and Argon2id hashing that transparently upgrades outdated hashes on sign-in.
- Short-lived JWT access tokens (15 min) and opaque, hashed **rotating refresh tokens** delivered as an
  `HttpOnly` cookie. Re-presenting a rotated token revokes the whole device session (theft detection),
  with a short grace window so two tabs refreshing at once don't sign the user out.
- CSRF protection for cookie-authenticated endpoints (double-submit token), remember-me sessions,
  sign-out of one or every device, device/session management and a per-user security audit log.
- Email verification and password reset with single-use, hashed, expiring tokens. Reset requests never
  reveal whether an account exists (constant response, lookup happens in a background job).
- Google sign-in via OpenID Connect with PKCE and a cookie-bound `state`, behind a provider interface;
  it switches itself off cleanly when credentials aren't configured.
- **Demo mode:** one click creates a throw-away account with ~90 nights of realistic, tagged history;
  a cron job deletes demo accounts after 24 hours.
- Redis rate limits (sliding window) on login (per IP and per email), registration, password reset and
  more; timing-safe login; strict CORS, security headers, request size limits, trusted hosts in production.

**Sleep data**
- Sleep sessions with quality, mood, latency, awakenings, notes and tags; validation of duration
  (10 min to 20 h), future timestamps and **overlaps** (checked in the service and enforced by a
  PostgreSQL exclusion constraint).
- Live sleep timer (start/stop/cancel) with one running timer per user guaranteed by a partial unique index.
- Filtering by night, quality, tags and notes text (trigram-indexed), sorting, pagination, bulk delete.
- Per-user tags (11 defaults seeded), goals with National Sleep Foundation recommendations by age,
  JSON/CSV export and CSV import with per-row error reporting.

**Analytics and insights**
- A 0-100 **sleep score** per night (duration vs goal, quality, latency, awakenings, bedtime adherence;
  missing inputs are re-weighted away).
- Overview, daily series, weekday patterns, distributions, tag impact and chronotype/social jetlag.
  Bedtimes are averaged with **circular statistics**, so 23:30 and 00:30 average to midnight.
- A pluggable insight provider with a deterministic, explainable rule engine.
- Weekly reports on demand or emailed automatically at 08:00 on the first day of each user's week,
  in their own timezone, and optional bedtime reminder emails 30 minutes before the goal bedtime.

**Operations**
- Liveness/readiness probes, Prometheus metrics, structured JSON logs with request ids, optional Sentry
  and OpenTelemetry tracing.
- ARQ worker for emails, reports and housekeeping crons.
- Multi-stage, non-root Docker image; Compose stack with migrations as a one-shot job.

## Architecture

```mermaid
flowchart LR
    browser([Browser]) --> next[Next.js frontend<br/>same-origin /api proxy]
    next -->|HTTP JSON| api

    subgraph backend [SleepTrack backend]
        api[FastAPI app<br/>routers to services to repositories]
        worker[ARQ worker<br/>jobs + crons]
    end

    api --> pg[(PostgreSQL 17)]
    api --> redis[(Redis 8)]
    worker --> pg
    worker --> redis
    api -- enqueue jobs --> redis
    redis -- jobs --> worker
    worker --> smtp[[SMTP / Mailpit]]
    api -. OAuth .-> google[[Google OIDC]]
    api -. metrics .-> prom[[Prometheus]]
```

Each feature lives in `app/modules/<name>/` with the same layers:

| Layer | Responsibility |
|---|---|
| `router.py` | HTTP only: parse input, call one service method, shape the response |
| `service.py` | Business rules and transactions; raises typed domain errors |
| `repository.py` | Queries (SQLAlchemy 2.0 style, no N+1) |
| `models.py` / `schemas.py` | ORM models / Pydantic request and response models |
| `dependencies.py` | FastAPI dependency wiring |

Analytics formulas are pure functions in `app/modules/analytics/calculations.py`, fed by a single
aggregate query and cached per user in Redis with version-based invalidation. See
[docs/architecture.md](docs/architecture.md) for the full picture and [docs/adr/](docs/adr/) for the
key decisions.

## Quick start

### With Docker (recommended)

Requirements: Docker with Compose v2.

```bash
git clone https://github.com/KhaledSaeed18/sleep-track-backend.git
cd sleep-track-backend
cp .env.example .env            # optional; every value has a development default
docker compose up -d --build    # postgres, redis, mailpit, migrate, api, worker
make seed                       # demo, admin and sample accounts
```

| Service | URL |
|---|---|
| API | http://localhost:8000 (docs at `/api/v1/docs`) |
| Mailpit (captured emails) | http://localhost:8025 |
| PostgreSQL | `localhost:5432` (user/password/db `sleeptrack`) |
| Redis | `localhost:6379` |

Seeded accounts:

| Email | Password | Role |
|---|---|---|
| `demo@sleeptrack.app` | `DemoPass123` | user, ~120 nights of tagged history |
| `admin@sleeptrack.app` | `AdminPass123` | admin |

### With uv (local development)

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (for Postgres/Redis/Mailpit).

```bash
make install    # Python 3.13, dependencies, git hooks
make infra      # postgres, redis and mailpit containers
make migrate
make seed
make dev        # uvicorn with auto-reload on :8000, jobs run in-process
```

If the full Compose stack is already running, stop its `api` container first (`docker compose stop api
worker`) because both use port 8000. To run the worker separately instead of in-process, start the API
without `JOBS_EAGER` and run `make worker`.

## Configuration

Settings are read from environment variables (or `.env`) and validated at startup; the app refuses to
start in production with a weak `SECRET_KEY`, `DEBUG=true`, wildcard CORS or the in-memory email
provider. The full list with comments lives in [`.env.example`](.env.example).

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `test`, `staging` or `production` |
| `SECRET_KEY` | dev placeholder | JWT signing key; at least 32 random characters in production |
| `DATABASE_URL` | `postgresql+asyncpg://sleeptrack:sleeptrack@localhost:5432/sleeptrack` | Async SQLAlchemy URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Rate limits, caches, token revocation, job queue |
| `FRONTEND_URL` | `http://localhost:3000` | Used for email links and OAuth redirects |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |
| `ALLOWED_HOSTS` | `*` | Trusted `Host` headers (enforced in production) |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | Access token lifetime |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | Remember-me session lifetime (sliding) |
| `REFRESH_SESSION_TTL_HOURS` | `24` | Non-remember-me session lifetime (sliding) |
| `REFRESH_REUSE_GRACE_SECONDS` | `10` | Window in which a rotated token is rejected without revoking the session |
| `COOKIE_SECURE` | auto | Defaults to `true` in staging and production |
| `REGISTRATION_OPEN` | `true` | Allow new sign-ups |
| `EMAIL_VERIFICATION_REQUIRED` | `false` | Block sign-in until the email is verified |
| `DEMO_MODE_ENABLED` | `true` | Enable `POST /auth/demo` |
| `RATE_LIMIT_ENABLED` | `true` | Toggle Redis rate limiting |
| `JOBS_EAGER` | `false` | Run background jobs in-process instead of via ARQ |
| `EMAIL_PROVIDER` | `console` | `console` or `smtp` (`memory` is for tests) |
| `SMTP_HOST` / `SMTP_PORT` | `localhost` / `1025` | SMTP server (Mailpit locally) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | unset | Enable Google sign-in when both are set |
| `OAUTH_REDIRECT_BASE_URL` | `FRONTEND_URL` | Origin used for the OAuth callback URL |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / auto | `console` in development, `json` elsewhere |
| `METRICS_ENABLED` | `true` | Expose `/metrics` |
| `SENTRY_DSN` | unset | Enable Sentry error reporting |
| `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` | `false` / unset | OpenTelemetry tracing over OTLP/HTTP |

## API overview

All endpoints live under `/api/v1` except `/health/*` and `/metrics`. Requests and responses are JSON
with snake_case keys; timestamps are ISO 8601 UTC.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `/login`, `/refresh`, `/logout`, `/logout-all`, `/demo`, `/verify-email`, `/verify-email/resend`, `/forgot-password`, `/reset-password`; `GET /auth/oauth/providers`, `/auth/oauth/{provider}/authorize`, `/callback` |
| Users | `GET/PATCH/DELETE /users/me`, `PATCH /users/me/preferences`, `POST /users/me/change-password`, `GET /users/me/sessions`, `DELETE /users/me/sessions/{id}`, `GET /users/me/audit-log`, `GET /users/me/export`, `POST /users/me/import` |
| Sleep | `GET/POST /sleep/sessions`, `GET/PATCH/DELETE /sleep/sessions/{id}`, `POST /sleep/sessions/bulk-delete`, `GET/DELETE /sleep/timer`, `POST /sleep/timer/start`, `/stop` |
| Tags and goals | `GET/POST /tags`, `PATCH/DELETE /tags/{id}`, `GET/PUT /goals` |
| Analytics | `GET /analytics/overview`, `/daily`, `/weekday`, `/tags`, `/distribution`, `/chronotype` |
| Insights and reports | `GET /insights`, `GET /reports/weekly`, `POST /reports/weekly/email` |
| Admin | `GET /admin/stats`, `GET /admin/users`, `PATCH /admin/users/{id}` |
| Meta | `GET /meta`, `GET /health/live`, `GET /health/ready`, `GET /metrics` |

A typical session:

```bash
# Sign in: the access token is in the body, refresh + CSRF cookies are set
curl -c jar -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"demo@sleeptrack.app","password":"DemoPass123","remember_me":true}'

# Call the API with the access token
curl localhost:8000/api/v1/analytics/overview -H "Authorization: Bearer $ACCESS_TOKEN"

# Rotate tokens: cookie + matching X-CSRF-Token header
curl -b jar -c jar -X POST localhost:8000/api/v1/auth/refresh \
  -H "X-CSRF-Token: $(awk '/st_csrf/ {print $7}' jar)"
```

Errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem details with a stable `code`:

```json
{
  "type": "https://sleeptrack.app/problems/session-overlap",
  "title": "Sleep session overlaps another session",
  "status": 409,
  "detail": "This session overlaps another session.",
  "instance": "/api/v1/sleep/sessions",
  "code": "session_overlap",
  "request_id": "6f1c2d0e9a7b4c3f8e21d5a09b7c4e11"
}
```

Operation ids follow `<tag>_<function>` (`sleep_list_sessions`, `auth_login`, ...), so generated
clients get readable method names. Regenerate the schema with `make openapi`.

## Testing and quality

```bash
make test        # full suite with coverage (needs Postgres and Redis: `make infra`)
make test-unit   # pure unit tests, no services
make lint        # ruff check + format check
make typecheck   # mypy --strict
make check       # all of the above
```

- **Integration tests** run against a real PostgreSQL database (`sleeptrack_test`, recreated and
  migrated with Alembic for every run, so the dev database stays untouched) and Redis database 15.
  They exercise every endpoint through `httpx.ASGITransport`, including token rotation and reuse
  detection, CSRF failures, rate limits, OAuth state handling, cross-user isolation and the jobs.
- **Unit tests** cover the analytics math, insight rules, security primitives, middleware, settings
  validation and integrations with fakeredis and mocked HTTP.
- Current suite: about 1,000 tests (roughly 200 integration, 800 unit) running in under 30 seconds,
  with 98 % line and branch coverage.
- CI enforces ruff, mypy strict, migrations that round-trip and match the models, an up-to-date OpenAPI
  schema, a coverage floor of 85 % and a Docker build.

## Deployment

The image is production-ready: multi-stage build with uv, Python 3.13 slim, non-root user, a
healthcheck and `ENVIRONMENT=production` by default.

```bash
docker build -t sleeptrack-api .
docker run --rm sleeptrack-api alembic upgrade head          # once per release
docker run -p 8000:8000 --env-file prod.env sleeptrack-api   # API
docker run --env-file prod.env sleeptrack-api arq app.worker.settings.WorkerSettings  # worker
```

Checklist:
1. Set a long random `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `FRONTEND_URL`, `CORS_ORIGINS`
   and `ALLOWED_HOSTS`; configure SMTP (`EMAIL_PROVIDER=smtp`).
2. Run migrations as a release step before rolling out new API containers.
3. Run at least one worker; crons are scheduled by ARQ (one worker process should own crons).
4. Terminate TLS at the proxy and set `FORWARDED_ALLOW_IPS` to the proxy's address so client IPs
   (used by rate limits and the audit log) are correct.
5. Keep `/metrics` on an internal network or behind the proxy's access rules.
6. Optional: `SENTRY_DSN`, `OTEL_ENABLED=true` with `OTEL_EXPORTER_OTLP_ENDPOINT`.

## Project structure

```
app/
├── main.py                 # create_app() factory and lifespan
├── api.py                  # versioned router composition
├── core/                   # config, security, errors, middleware, logging, rate limiting, pagination
├── db/                     # declarative base, session/unit of work, model registry, types
├── modules/
│   ├── auth/               # sessions, refresh tokens, CSRF, OAuth, demo, audit trail
│   ├── users/              # profile, preferences, devices, export/import, deletion
│   ├── sleep/              # sessions, timer, scoring, demo data generator
│   ├── tags/  goals/       # tags with defaults; goals and age recommendations
│   ├── analytics/          # pure calculations, builders, cache, endpoints
│   ├── insights/           # provider protocol and rule engine
│   ├── reports/            # weekly reports
│   ├── admin/  meta/       # admin endpoints; meta and health probes
├── integrations/           # email providers + templates renderer, OAuth providers
├── templates/email/        # Jinja2 HTML and text templates
├── worker/                 # ARQ settings, tasks, job queue abstraction
└── scripts/                # seed, create_admin, export_openapi
migrations/                 # Alembic (async env, naming conventions)
tests/
├── unit/                   # pure functions, no services
└── integration/            # HTTP API against Postgres and Redis
docs/                       # architecture, analytics formulas, ADRs, OpenAPI schema
```

## Further reading

- [Architecture](docs/architecture.md)
- [Analytics and scoring formulas](docs/analytics.md)
- [Architecture decision records](docs/adr/)
- [Security policy](SECURITY.md) and [contributing guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE) © Khaled Saeed
