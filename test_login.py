import asyncio
import os
import sys
import httpx

async def run():
    async with httpx.AsyncClient() as client:
        payload = {
            "email": "manikumar96462@gmail.com",
            "password": "Password123!"
        }
        
        print(f"Attempting login for {payload['email']}...")
        
        try:
            response = await client.post("http://localhost:8000/api/v1/auth/login", json=payload)
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            
        except httpx.HTTPError as e:
            print(f"HTTP Error: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run())
