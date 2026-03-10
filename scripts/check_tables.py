
import asyncio
from sqlalchemy import text
from app.db.session import engine

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema';"))
        tables = result.fetchall()
        print("Tables in DB:")
        for t in tables:
            print(f"- {t[0]}")

if __name__ == "__main__":
    asyncio.run(check())
