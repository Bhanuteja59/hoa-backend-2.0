import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def run():
    async with AsyncSessionLocal() as db:
        try:
            await db.execute(text("ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS account_number VARCHAR(50);"))
            await db.execute(text("ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS registration_number VARCHAR(50);"))
            await db.execute(text("ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS invitation_token VARCHAR(100);"))
            await db.execute(text("ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS invite_sent_at TIMESTAMP WITH TIME ZONE;"))
            await db.execute(text("ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS invitation_expires_at TIMESTAMP WITH TIME ZONE;"))
            await db.commit()
            print("Successfully added columns!")
        except Exception as e:
            print("Error:", e)
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(run())
