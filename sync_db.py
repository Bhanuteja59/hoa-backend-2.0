import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal
from app.db.models import Notification, Base
from app.db.session import engine

async def recreate_table():
    async with engine.begin() as conn:
        try:
            # We use Notification.__table__.create(conn) to be safe or just create all missing
            # But let's just use the DDL for Notification specifically to be surgical
            # Or run create_all which only creates missing ones
            await conn.run_sync(Base.metadata.create_all)
            print("Database sync completed (Missing tables created).")
        except Exception as e:
            print(f"Error syncing database: {e}")

if __name__ == "__main__":
    asyncio.run(recreate_table())
