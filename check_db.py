import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def check_table():
    async with AsyncSessionLocal() as db:
        try:
            res = await db.execute(text("SELECT count(*) FROM notifications"))
            count = res.scalar()
            print(f"Table 'notifications' exists. Count: {count}")
        except Exception as e:
            print(f"Error checking table: {e}")

if __name__ == "__main__":
    asyncio.run(check_table())
