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
            full_name='Mani Updated', # updated name
            password='NewPassword123!',
            hoa_name='kA',
            role='USER',
            tenant_slug='5A3D87CF',
            account_number='175134203185',
            registration_number='308757'  # DB value for this user
        )
        try:
            await AuthService.register(payload, db)
            print("Successfully registered and merged details!")
        except Exception as e:
            import traceback
            with open("error_trace_final.txt", "w", encoding="utf-8") as f:
                traceback.print_exc(file=f)

if __name__ == "__main__":
    asyncio.run(run())
