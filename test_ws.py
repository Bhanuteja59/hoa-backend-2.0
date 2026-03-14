import asyncio
import websockets

async def test_ws():
    uri = "ws://localhost:8000/api/v1/notifications/ws/5eeca3a0-35cd-4952-b28e-9ee0fdfdb6db"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected successfully!")
            # Maybe send something or just wait
            await asyncio.sleep(1)
    except Exception as e:
        print(f"Failed to connect: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
