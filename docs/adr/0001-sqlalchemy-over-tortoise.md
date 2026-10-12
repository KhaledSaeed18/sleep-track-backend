# 0001. SQLAlchemy 2.x and Alembic instead of Tortoise ORM

- Status: accepted
- Date: 2026-10-12

## Context

Version 1 of the API used Tortoise ORM with aerich migrations. It was quick to start with, but v2
needs things that pushed against it:

- Database-level integrity for the domain: check constraints, a partial unique index (one running
  timer per user), a GiST exclusion constraint against overlapping sessions, functional unique
  indexes on `lower(email)`, and trigram indexes for search.
- Aggregate queries for analytics (`array_agg`, grouped counts, correlated subqueries) without
  falling back to raw SQL strings.
- Reliable, reviewable migrations with autogenerate and a way to check in CI that models and
  migrations agree.
- First-class static typing; the project runs mypy in strict mode.

## Decision

Use SQLAlchemy 2.x with the asyncio extension and the asyncpg driver, typed `Mapped[...]` models,
and Alembic with an async `env.py`, autogenerate and a naming convention for constraints.

Repositories own queries; services own transactions and call `commit()` explicitly on the
request-scoped `AsyncSession`.

## Consequences

- Constraints that matter are enforced by PostgreSQL and mirrored in the services for friendly
  errors; races (two concurrent inserts) are caught by the database and mapped to the same error codes.
- Alembic autogenerate does not handle every construct perfectly (enum check constraints were
  duplicated and extensions must be created by hand), so generated migrations are reviewed before
  committing. CI runs `upgrade`, `check` and `downgrade` to keep them honest.
- Async SQLAlchemy forbids implicit lazy loading. Relationships are loaded explicitly (`selectin`,
  `joined`, or `raiseload` where a load would be a bug), and timestamps are generated in Python so
  instances are complete after a flush.
- SQLAlchemy is more verbose than Tortoise, but its query builder covers every analytics query
  without raw SQL and its typing works with mypy strict.
