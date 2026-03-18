import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from sqlalchemy import select
from app.db import async_session_factory
from app.models.user import AdminUser, AdminRole, AdminUserStatus
from app.core.security import hash_password


async def create_superuser():
    async with async_session_factory() as session:
        result = await session.execute(
            select(AdminUser).where(AdminUser.email == "engineering@opensaucery.africa")
        )
        user = result.scalar_one_or_none()

        if user:
            print("Superuser already exists.")
            return

        print("Creating superuser...")
        # Create user with active status
        new_user = AdminUser(
            email="engineering@opensaucery.africa",
            name="Super Admin",
            password_hash=hash_password("opensaucery"),
            role=AdminRole.SUPER_ADMIN,
            status=AdminUserStatus.ACTIVE,
            totp_enabled=False,
        )
        session.add(new_user)
        await session.commit()
        print("Superuser created successfully.")
        print("Email: engineering@opensaucery.africa")
        print("Password: opensaucery")


if __name__ == "__main__":
    asyncio.run(create_superuser())
