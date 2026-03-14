from app.main import app
from starlette.routing import WebSocketRoute

print("All Routes:")
for route in app.routes:
    if isinstance(route, WebSocketRoute):
        print(f"WS Path: {route.path}")
    else:
        print(f"HTTP Path: {route.path}")

print("\nSpecific Check for notifications/ws:")
found = False
for route in app.routes:
    if "/notifications/ws" in route.path:
        print(f"FOUND: {route.path} ({'WS' if isinstance(route, WebSocketRoute) else 'HTTP'})")
        found = True
if not found:
    print("NOT FOUND: /notifications/ws")
