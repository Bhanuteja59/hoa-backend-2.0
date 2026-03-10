import asyncio
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select
import sys
import os

# Ensure app is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import AsyncSessionLocal
from app.db.models import User, Tenant, TenantUser
from app.core.security import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def seed():
    logger.info("Starting database seeding...")
    async with AsyncSessionLocal() as db:
        try:
            # 1. Check/Create User
            res = await db.execute(select(User).where(User.email == "ravi@gmail.com"))
            user = res.scalar_one_or_none()
            
            if not user:
                logger.info("Creating default superadmin...")
                user = User(
                    id=uuid.uuid4(),
                    email="ravi@gmail.com",
                    name="Super Admin",
                    password_hash=hash_password("11111111"),
                    is_platform_admin=True,
                    created_at=datetime.now(timezone.utc)
                )
                db.add(user)
                await db.flush()
            else:
                # Ensure is_platform_admin is set if it already exists
                if not user.is_platform_admin:
                    user.is_platform_admin = True
                    db.add(user)
                    await db.flush()

            # 2. Check/Create Platform Tenant (Required for login)
            res = await db.execute(select(Tenant).where(Tenant.slug == "platform"))
            tenant = res.scalar_one_or_none()
            
            if not tenant:
                tenant = Tenant(
                    id=uuid.uuid4(),
                    slug="platform",
                    name="Platform Admin",
                    status="ACTIVE",
                    created_at=datetime.now(timezone.utc)
                )
                db.add(tenant)
                await db.flush()

            # 3. Link User to Tenant
            res = await db.execute(select(TenantUser).where(
                TenantUser.user_id == user.id,
                TenantUser.tenant_id == tenant.id
            ))
            tu = res.scalar_one_or_none()
            
            if not tu:
                tu = TenantUser(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    user_id=user.id,
                    roles=["ADMIN", "SUPER_ADMIN"],
                    status="active",
                    created_at=datetime.now(timezone.utc)
                )
                db.add(tu)
                await db.commit()
                logger.info("Default Super Admin ready: ravi@gmail.com")

            # 4. FIX/HEAL accidental Super Admins
            # Any user who is platform admin BUT not the root admin should be demoted.
            logger.info("Healing permissions...")
            res = await db.execute(select(User).where(User.is_platform_admin == True))
            admins = res.scalars().all()
            for a in admins:
                if a.email != "ravi@gmail.com":
                    logger.info(f"Revoking Super Admin from {a.email}")
                    a.is_platform_admin = False
                    db.add(a)
            await db.commit()
            logger.info("Permissions verified.")
            
        except Exception as e:
            logger.error(f"Seeding error: {e}")
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(seed())
