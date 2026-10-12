"""Shared test configuration.

Environment variables are set before the application is imported so settings
pick up the test database (``sleeptrack_test``), a dedicated Redis database,
eager background jobs and an in-memory email outbox.
"""

from __future__ import annotations

import os

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://sleeptrack:sleeptrack@localhost:5432/sleeptrack_test",
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
        "EMAIL_PROVIDER": "memory",
        "JOBS_EAGER": "true",
        "RATE_LIMIT_ENABLED": "false",
        "ARGON2_TIME_COST": "1",
        "ARGON2_MEMORY_COST": "1024",
        "ARGON2_PARALLELISM": "1",
        "LOG_LEVEL": "WARNING",
        "LOG_FORMAT": "json",
        "FRONTEND_URL": "http://localhost:3000",
        "CORS_ORIGINS": "http://localhost:3000",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256-signing",
    }
)
