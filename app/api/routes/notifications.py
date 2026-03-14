from __future__ import annotations
from typing import List
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.db.models import Notification, TenantUser
from app.services.notifications import notification_manager

router = APIRouter(tags=["notifications"])

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket endpoint for real-time notifications.
    Auth should ideally be handled via a ticket or token in subprotocols, 
    but for now we'll use user_id from path.
    """
    await notification_manager.connect(user_id, websocket)
    try:
        while True:
            # Keep connection alive and listen for any client messages (future: "seen" acknowledgments)
            data = await websocket.receive_text()
            # We can pulse check or handle acknowledgments here
    except WebSocketDisconnect:
        notification_manager.disconnect(user_id, websocket)

@router.get("")
async def list_notifications(
    limit: int = Query(20, gt=0, le=100),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("notifications:read")),
):
    """
    Fetch notification history for the current user.
    """
    stmt = (
        select(Notification)
        .where(
            Notification.tenant_id == UUID(tenant.tenant_id),
            Notification.user_id == UUID(ctx.user_id)
        )
        .order_by(desc(Notification.created_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    notifications = res.scalars().all()
    
    return [
        {
            "id": str(n.id),
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "link": n.link,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat()
        } for n in notifications
    ]

@router.post("/read")
async def mark_as_read(
    notification_ids: List[str] | None = None,
    all: bool = False,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("notifications:write")),
):
    """
    Mark specific or all notifications as read.
    """
    stmt = update(Notification).where(
        Notification.tenant_id == UUID(tenant.tenant_id),
        Notification.user_id == UUID(ctx.user_id)
    ).values(is_read=True)
    
    if not all and notification_ids:
        ids = [UUID(id_str) for id_str in notification_ids]
        stmt = stmt.where(Notification.id.in_(ids))
    
    await db.execute(stmt)
    await db.commit()
    return {"status": "success"}

@router.get("/unread-count")
async def get_unread_count(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("notifications:read")),
):
    from sqlalchemy import func
    stmt = select(func.count(Notification.id)).where(
        Notification.tenant_id == UUID(tenant.tenant_id),
        Notification.user_id == UUID(ctx.user_id),
        Notification.is_read == False
    )
    res = await db.execute(stmt)
    count = res.scalar()
    return {"unread_count": count}
