
import asyncio
import sys
import os

# Add parent dir to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import engine
from app.db.models import Base

async def init_db():
    print("Initializing Analytics Table...")
    async with engine.begin() as conn:
        # This will create tables that don't exist yet
        await conn.run_sync(Base.metadata.create_all)
    print("Done!")

if __name__ == "__main__":
    asyncio.run(init_db())
