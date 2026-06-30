"""Bootstrap the first platform administrator.

Reads credentials from the ``BOOTSTRAP_ADMIN_EMAIL`` / ``BOOTSTRAP_ADMIN_PASSWORD``
environment variables (or their equivalents in ``Settings``), creating a
SUPER_ADMIN on a fresh deployment. Idempotent — exits without touching the
database if an admin with that email already exists.

The operator is expected to log in once with these credentials and rotate
the password immediately. After that, subsequent admins should be created
through the ``POST /admin/admins`` API.

Usage:
    BOOTSTRAP_ADMIN_EMAIL=admin@client.example \\
    BOOTSTRAP_ADMIN_PASSWORD='temporary-rotate-me' \\
    python scripts/create_superuser.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import async_session_factory  # noqa: E402
from app.models.user import AdminRole, AdminUser, AdminUserStatus  # noqa: E402


async def create_superuser() -> int:
    settings = get_settings()
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password or ""
    name = settings.bootstrap_admin_name or "Platform Administrator"

    if not email or not password:
        print(
            "[bootstrap] BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD not set — "
            "skipping superuser creation. Set both to mint the first admin.",
            file=sys.stderr,
        )
        return 0

    if len(password) < 8:
        print(
            "[bootstrap] BOOTSTRAP_ADMIN_PASSWORD must be at least 8 characters.",
            file=sys.stderr,
        )
        return 1

    async with async_session_factory() as session:
        existing = (
            await session.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if existing:
            print(f"[bootstrap] Superuser '{email}' already exists — nothing to do.")
            return 0

        session.add(
            AdminUser(
                email=email,
                name=name,
                password_hash=hash_password(password),
                role=AdminRole.SUPER_ADMIN,
                status=AdminUserStatus.ACTIVE,
                totp_enabled=False,
            )
        )
        await session.commit()

    print(f"[bootstrap] Created SUPER_ADMIN '{email}'. Log in and rotate the password immediately.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(create_superuser()))
