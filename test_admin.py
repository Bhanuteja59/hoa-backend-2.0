import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import AsyncSessionLocal
from app.services.auth_service import AuthService
from app.api.routes.auth import RegisterIn

async def run():
    async with AsyncSessionLocal() as db:
        payload = RegisterIn(
            email='manikumar96462@gmail.com', # the existing active user
            full_name='Mani Admin 2', # updated name
            password='Password123!',
            hoa_name='Third HOA Community',
            community_type='OWN_HOUSES',
            phone='1234567890',
            role='BOARD_ADMIN'
        )
        try:
            await AuthService.register(payload, db)
            print("Successfully registered admin!")
        except Exception as e:
            import traceback
            with open("error_trace_admin.txt", "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)

if __name__ == "__main__":
    asyncio.run(run())
