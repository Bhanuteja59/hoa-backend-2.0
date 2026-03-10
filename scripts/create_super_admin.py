
import asyncio
import uuid
from datetime import datetime
from sqlalchemy import select
from app.db.session import engine, AsyncSessionLocal
from app.db.models import User
from app.core.security import hash_password

async def create_super_admin():
    email = "bristechdevelopers@gmail.com"
    password = "Bris@Developers#123"
    name = "Super Admin"

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.email == email))
        user = res.scalar_one_or_none()

        if user:
            print(f"User {email} already exists. Updating to Super Admin...")
            user.is_platform_admin = True
            user.password_hash = hash_password(password)
            user.name = name
            db.add(user)
        else:
            print(f"Creating Super Admin {email}...")
            user = User(
                id=uuid.uuid4(),
                email=email,
                name=name,
                password_hash=hash_password(password),
                is_platform_admin=True,
                created_at=datetime.utcnow()
            )
            db.add(user)
        
        await db.commit()
        print("Super Admin created/updated successfully!")

if __name__ == "__main__":
    asyncio.run(create_super_admin())
