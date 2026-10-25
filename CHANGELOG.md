# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Use Python 3.13 in the Docker runtime so installed dependencies load and database migrations start.

## [2.0.0] - 2026-10-12

A ground-up rewrite. The v1 API is not compatible with v2; the new contract is the OpenAPI
schema in [`docs/openapi.json`](docs/openapi.json), which the frontend generates its types from.

### Added
- FastAPI application factory with lifespan-managed resources, versioned `/api/v1` routes and clean
  OpenAPI operation ids.
- Authentication: registration with password policy, login with remember-me, 15-minute JWT access
  tokens, rotating hashed refresh tokens with reuse detection and family revocation, CSRF
  double-submit, sign-out of one or all devices, email verification, enumeration-safe password reset,
  Google OAuth (PKCE + state), demo accounts with generated history, device session management and a
  security audit log.
- Users: profile and preferences, change or set password, JSON/CSV export, CSV import with per-row
  errors, account deletion.
- Sleep sessions with validation (duration, future end, overlap), filtering, sorting, search,
  pagination and bulk delete; a live sleep timer.
- Tags with seeded defaults and usage counts; goals with National Sleep Foundation recommendations.
- Analytics: sleep score, overview with circular bedtime statistics, consistency, sleep debt, streaks
  and previous-period comparison; daily, weekday, distribution, tag impact and chronotype endpoints;
  per-user Redis caching.
- Insights through a pluggable provider with a deterministic rule engine.
- Weekly reports on demand and by email; hourly dispatch in each user's timezone.
- Optional bedtime reminder emails 30 minutes before the goal bedtime.
- Admin statistics and user management with role-based access.
- ARQ worker with email, password-reset and report jobs and housekeeping crons.
- RFC 9457 problem details with stable error codes; request ids; security headers; body size limit;
  Redis sliding-window rate limits; structured logging; Prometheus metrics; optional Sentry and
  OpenTelemetry.
- PostgreSQL schema with check constraints, an exclusion constraint against overlapping sessions, a
  partial unique index for running timers and trigram search, managed by Alembic.
- Seed script with demo, admin and sample accounts; admin creation and OpenAPI export scripts.
- Multi-stage Docker image, Compose stack, Makefile, pre-commit hooks, GitHub Actions CI and
  Dependabot.
- Test suite against real PostgreSQL and Redis, plus unit tests for the analytics math.
- Documentation: README, architecture, analytics formulas, ADRs, security policy and contributing
  guide.

### Changed
- Replaced Tortoise ORM and aerich with SQLAlchemy 2.x (async) and Alembic.
- Replaced python-jose and passlib/bcrypt with PyJWT and pwdlib/Argon2.
- Dependency management moved from `requirements.txt` to uv with a lockfile.

### Removed
- The v1 endpoints and data model.

[2.0.0]: https://github.com/KhaledSaeed18/sleep-track-backend/releases/tag/v2.0.0
