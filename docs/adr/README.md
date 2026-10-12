# Architecture decision records

Short records of decisions that shaped the backend, in the format described by Michael Nygard:
context, decision, consequences. New ADRs get the next number; superseded ones stay in place with a
link to their replacement.

| # | Decision | Status |
|---|---|---|
| [0001](0001-sqlalchemy-over-tortoise.md) | Use SQLAlchemy 2.x (async) and Alembic instead of Tortoise ORM | Accepted |
| [0002](0002-token-strategy.md) | Short-lived JWT access tokens with rotating, hashed refresh tokens in cookies | Accepted |
| [0003](0003-problem-details.md) | RFC 9457 problem details for every error | Accepted |
| [0004](0004-arq-background-jobs.md) | ARQ on Redis for background jobs and crons | Accepted |
