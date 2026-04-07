import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

import bcrypt
from sqlalchemy import select
from app.db import async_session_factory
from app.models.user import AdminUser, AdminRole, AdminUserStatus


def hash_pw(password: str) -> str:
    """Hash password using bcrypt directly (avoids passlib compat issues)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def create_admin():
    async with async_session_factory() as session:
        result = await session.execute(
            select(AdminUser).where(AdminUser.email == "admin@payswitch.com")
        )
        user = result.scalar_one_or_none()

        if user:
            print("Admin user admin@payswitch.com already exists.")
            return

        print("Creating admin user...")
        new_user = AdminUser(
            email="admin@payswitch.com",
            name="PaySwitch Admin",
            password_hash=hash_pw("payswitch2026"),
            role=AdminRole.SUPER_ADMIN,
            status=AdminUserStatus.ACTIVE,
            totp_enabled=False,
        )
        session.add(new_user)
        await session.commit()
        print("Admin user created successfully.")
        print("Email: admin@payswitch.com")
        print("Password: payswitch2026")


if __name__ == "__main__":
    asyncio.run(create_admin())
