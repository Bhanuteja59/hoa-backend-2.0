import json
import logging
from typing import Dict, List, Any
from fastapi import WebSocket
from uuid import UUID

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self):
        # active_connections: { user_id: [WebSocket, ...] }
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"WebSocket connected for user {user_id}. Active sessions: {len(self.active_connections[user_id])}")

    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"WebSocket disconnected for user {user_id}")

    async def broadcast_to_user(self, user_id: str, message: Dict[str, Any]):
        """
        Send a real-time message to all active WebSocket sessions of a specific user.
        """
        uid_str = str(user_id)
        if uid_str in self.active_connections:
            # Create a copy of the list to avoid issues if a client disconnects during broadcast
            sockets = list(self.active_connections[uid_str])
            for websocket in sockets:
                try:
                    await websocket.send_text(json.dumps(message))
                except Exception as e:
                    logger.error(f"Error sending WebSocket message to user {uid_str}: {e}")
                    self.disconnect(uid_str, websocket)

    async def notify_user(
        self, 
        user_id: UUID, 
        title: str, 
        message: str, 
        type: str = "system", 
        link: str = None
    ):
        """
        Helper to format and send a notification payload.
        """
        payload = {
            "type": "NOTIFICATION",
            "data": {
                "title": title,
                "message": message,
                "category": type,
                "link": link,
                "timestamp": "now" # Frontend can handle formatting
            }
        }
        await self.broadcast_to_user(str(user_id), payload)

# Global singleton instance
notification_manager = NotificationManager()
