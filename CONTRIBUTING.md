# Contributing

Thanks for taking the time to contribute. This guide covers the local workflow and the conventions
the code base follows.

## Setup

```bash
make install   # Python 3.13 via uv, dependencies, pre-commit hooks
make infra     # Postgres, Redis and Mailpit in Docker
make migrate && make seed
make dev       # API on :8000 with auto-reload
```

## Workflow

1. Branch from the default branch: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
2. Keep changes focused; one logical change per commit.
3. Run `make check` (ruff, mypy strict, tests with coverage) before pushing.
4. If you change the API, run `make openapi` and commit `docs/openapi.json` (CI fails on drift).
5. If you change models, add a migration with `make migration m="describe the change"`, review the
   generated file, and make sure `uv run alembic check` reports no differences.
6. Open a pull request describing the change and how you tested it.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(sleep): allow editing the start of a running timer
fix(auth): clear cookies when a refresh fails
test(analytics): cover circular means across midnight
docs: explain the sleep score weights
chore(deps): bump fastapi
```

Common types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, `style`.
Scopes are module names (`auth`, `users`, `sleep`, `tags`, `goals`, `analytics`, `insights`,
`reports`, `admin`, `core`, `db`, `worker`).

## Code conventions

- **Layers**: routers are thin; business rules live in services, which raise `AppError` subclasses;
  queries live in repositories. Pure computation (analytics, scoring) has no I/O.
- **Errors**: never return ad-hoc error bodies. Add a typed exception with a stable `code` when a new
  failure needs to be distinguished by clients, and document it.
- **Typing**: the project runs `mypy --strict`; avoid `Any` and `type: ignore` without an error code.
- **Database**: use SQLAlchemy 2.0-style queries, avoid N+1 (prefer one aggregate query or
  `selectinload`), and enforce invariants with constraints as well as in code.
- **Comments** explain why, not what. Keep them short.
- **Tests**: every endpoint change comes with integration tests (including the unhappy paths and
  cross-user isolation); pure functions get unit tests. Integration tests need `make infra`.

## Reporting bugs

Open an issue with steps to reproduce, the expected and actual behaviour, and the `request_id` from
the error response if you have one. Security issues: see [SECURITY.md](SECURITY.md).
