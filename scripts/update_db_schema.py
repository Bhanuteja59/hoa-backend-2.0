
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import engine
from sqlalchemy import text

async def upgrade_db():
    async with engine.begin() as conn:
        print("Checking for community_type column...")
        # Check if column exists to avoid error
        result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='tenants' AND column_name='community_type'"))
        if result.scalar():
            print("Column 'community_type' already exists.")
        else:
            print("Adding 'community_type' column to 'tenants' table...")
            await conn.execute(text("ALTER TABLE tenants ADD COLUMN community_type VARCHAR(32) DEFAULT 'APARTMENTS' NOT NULL"))
            print("Column added successfully.")

if __name__ == "__main__":
    asyncio.run(upgrade_db())
