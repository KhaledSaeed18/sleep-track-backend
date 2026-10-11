"""Create an administrator, or promote an existing account.

python -m app.scripts.create_admin --email ops@example.com --password '...'
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.config import get_settings
from app.core.passwords import password_problems
from app.core.security import get_password_hasher
from app.db.session import create_engine, create_session_factory
from app.modules.users.models import UserRole
from app.modules.users.provisioning import provision_user
from app.modules.users.repository import UserRepository


async def create_admin(email: str, password: str | None, first_name: str, last_name: str) -> str:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            existing = await UserRepository(session).get_by_email(email)
            if existing is not None:
                existing.role = UserRole.ADMIN
                existing.is_active = True
                await session.commit()
                return f"Promoted {existing.email} to admin."
            if not password:
                raise SystemExit("A password is required to create a new account.")
            problems = password_problems(password)
            if problems:
                raise SystemExit(f"Password {'; '.join(problems)}.")
            user = await provision_user(
                session,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password_hash=get_password_hasher(settings).hash(password),
                role=UserRole.ADMIN,
                is_email_verified=True,
            )
            await session.commit()
            return f"Created admin {user.email}."
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="Prompted for when omitted")
    parser.add_argument("--first-name", default="Admin")
    parser.add_argument("--last-name", default="")
    args = parser.parse_args(argv)
    password = args.password
    if password is None and sys.stdin.isatty():
        password = getpass.getpass("Password (leave empty to only promote): ") or None
    print(asyncio.run(create_admin(args.email, password, args.first_name, args.last_name)))


if __name__ == "__main__":
    main()
