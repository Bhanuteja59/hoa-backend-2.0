# backend/app/api/routes/announcements.py
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.db.models import Announcement
from sqlalchemy import delete, update

router = APIRouter(prefix="/announcements", tags=["announcements"])

class AnnouncementIn(BaseModel):
    title: str
    body: str
    audience: str = "ALL"  # ALL|RESIDENTS|BOARD
    publish: bool = True
    event_date: datetime | None = None

@router.post("")
async def create_announcement(
    payload: AnnouncementIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("announcements:write")),
):
    now = datetime.now(timezone.utc)
    a = Announcement(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        title=payload.title,
        body=payload.body,
        audience=payload.audience,
        published_at=now if payload.publish else None,
        event_date=payload.event_date,
        created_by_user_id=UUID(ctx.user_id),
        created_at=now,
    )
    db.add(a)
    await db.commit()
    return {"id": str(a.id)}

@router.get("")
async def list_announcements(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("announcements:read")),
    limit: int = 100,
    upcoming: bool = False,
):
    query = select(Announcement).where(Announcement.tenant_id == tenant.tenant_id)
    
    if upcoming:
        # Show upcoming events AND undated announcements (news)
        # Sort by event_date ASC (soonest first), putting NULLs (general news) at the top? 
        # Or NULLs at bottom?
        # User said "ascending based on the current future dates".
        # Let's put general news at top (pinned-like) then upcoming events.
        from sqlalchemy import or_
        now = datetime.now(timezone.utc)
        
        # Filter: event_date is NULL OR event_date >= now
        query = query.where(or_(Announcement.event_date == None, Announcement.event_date >= now))
        
        # Sort: NULLs first (News), then Earliest Event
        query = query.order_by(Announcement.event_date.asc().nullsfirst(), Announcement.created_at.desc())
    else:
        # Default: content descending
        query = query.order_by(Announcement.event_date.desc().nullslast(), Announcement.published_at.desc().nullslast(), Announcement.created_at.desc())

    query = query.limit(limit)

    # Filter for residents (not admin/board)
    has_privileged_access = "ADMIN" in ctx.roles or "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not has_privileged_access:
        query = query.where(Announcement.audience.in_(["ALL", "RESIDENTS"]))

    res = await db.execute(query)
    return [
        {"id": str(a.id), "title": a.title, "audience": a.audience, "published_at": a.published_at, "event_date": a.event_date, "created_at": a.created_at, "body": a.body}
        for a in res.scalars().all()
    ]

@router.put("/{announcement_id}")
async def update_announcement(
    announcement_id: str,
    payload: AnnouncementIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("announcements:write")),
):
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can edit", status_code=403)
        
    res = await db.execute(select(Announcement).where(
        Announcement.tenant_id == tenant.tenant_id,
        Announcement.id == UUID(announcement_id)
    ))
    a = res.scalar_one_or_none()
    if not a:
        raise AppError(code="NOT_FOUND", message="Announcement not found", status_code=404)
        
    a.title = payload.title
    a.body = payload.body
    a.audience = payload.audience
    a.published_at = datetime.now(timezone.utc) if payload.publish else None
    a.event_date = payload.event_date
    
    await db.commit()
    return {"ok": True}

@router.delete("/{announcement_id}")
async def delete_announcement(
    announcement_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("announcements:write")),
):
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can delete", status_code=403)

    res = await db.execute(select(Announcement).where(
        Announcement.tenant_id == tenant.tenant_id,
        Announcement.id == UUID(announcement_id)
    ))
    a = res.scalar_one_or_none()
    if not a:
        raise AppError(code="NOT_FOUND", message="Announcement not found", status_code=404)
        
    await db.delete(a)
    await db.commit()
    return {"ok": True}
