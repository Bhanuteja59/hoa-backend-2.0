import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import AsyncSessionLocal
from app.db.models import User, TenantUser
from sqlalchemy import select

async def run():
    async with AsyncSessionLocal() as db:
        email = 'manikumar96462@gmail.com'
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one_or_none()
        
        if user:
            print(f"User exists: {user.id}")
            tu_res = await db.execute(select(TenantUser).where(TenantUser.user_id == user.id))
            tus = tu_res.scalars().all()
            print(f"TenantUsers count: {len(tus)}")
            for tu in tus:
                print(f" - Tenant: {tu.tenant_id}, Status: {tu.status}, Reg: {tu.registration_number}, Acct: {tu.account_number}")
        else:
            print("User does not exist")

if __name__ == "__main__":
    asyncio.run(run())
