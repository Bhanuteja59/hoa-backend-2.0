# backend/app/api/routes/work_orders.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.db.models import WorkOrder, WorkOrderEvent, TenantUser, User, Unit
from sqlalchemy import delete

router = APIRouter(prefix="/work-orders", tags=["work_orders"])

class WorkOrderIn(BaseModel):
    title: str
    description: str
    attachment_url: Optional[str] = None
    unit_id: str | None = None  # residents omit; server derives

class WorkOrderPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    assigned_to_user_id: str | None = None
    message: str | None = None
    attachment_url: str | None = None

@router.post("")
async def create_work_order(
    payload: WorkOrderIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("work_orders:write")),
):
    unit_id = payload.unit_id
    if "USER" in ctx.roles:
        tu = (await db.execute(select(TenantUser).where(
            TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id)
        ))).scalar_one_or_none()
        if not tu or not tu.unit_id:
            raise AppError(code="UNIT_MISSING", message="Resident unit not configured", status_code=400)
        unit_id = str(tu.unit_id)

    if not unit_id:
        raise AppError(code="UNIT_REQUIRED", message="unit_id required", status_code=400)

    now = datetime.now(timezone.utc)
    wo = WorkOrder(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        unit_id=UUID(unit_id),
        created_by_user_id=UUID(ctx.user_id),
        title=payload.title,
        description=payload.description,
        attachment_url=payload.attachment_url,
        status="NEW",
        priority="NORMAL",
        assigned_to_user_id=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )
    db.add(wo)
    db.add(WorkOrderEvent(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        work_order_id=wo.id,
        actor_user_id=UUID(ctx.user_id),
        type="CREATED",
        message=None,
        created_at=now,
    ))
    await db.commit()
    return {"id": str(wo.id)}

@router.get("")
async def list_work_orders(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("work_orders:read")),
):
    stmt = (
        select(WorkOrder, User.name, Unit.unit_number)
        .join(User, WorkOrder.created_by_user_id == User.id)
        .join(Unit, WorkOrder.unit_id == Unit.id)
        .where(WorkOrder.tenant_id == UUID(tenant.tenant_id))
    )
    is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
    if "USER" in ctx.roles and not is_admin_or_board:
        stmt = stmt.where(WorkOrder.created_by_user_id == UUID(ctx.user_id))
    if status:
        stmt = stmt.where(WorkOrder.status == status)
        
    res = await db.execute(stmt.order_by(WorkOrder.updated_at.desc()).limit(limit))
    rows = res.all()
    return [
        {
            "id": str(w.id),
            "unit_id": str(w.unit_id),
            "title": w.title,
            "description": w.description,
            "attachment_url": w.attachment_url,
            "status": w.status,
            "priority": w.priority,
            "updated_at": w.updated_at,
            "user_name": user_name,
            "unit_number": unit_number
        }
        for w, user_name, unit_number in rows
    ]

@router.patch("/{work_order_id}")
async def update_work_order(
    work_order_id: str,
    payload: WorkOrderPatch,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("work_orders:write")),
):
    res = await db.execute(select(WorkOrder).where(
        WorkOrder.tenant_id == UUID(tenant.tenant_id), WorkOrder.id == UUID(work_order_id)
    ))
    wo = res.scalar_one_or_none()
    if not wo:
        raise AppError(code="NOT_FOUND", message="Work order not found", status_code=404)

    now = datetime.now(timezone.utc)
    
    # RBAC Check: Residents cannot assign users, but can update description/title
    # RBAC Check: Residents cannot assign users or change status
    if "USER" in ctx.roles and "ADMIN" not in ctx.roles and "BOARD" not in ctx.roles and "BOARD_MEMBER" not in ctx.roles:
       if payload.assigned_to_user_id is not None:
            raise AppError(code="NO_PERMISSION", message="Residents cannot assign work orders", status_code=403)
       if payload.status is not None and payload.status != wo.status:
            # Residents might be allowed to CANCEL, but let's be strict for now
            raise AppError(code="NO_PERMISSION", message="Residents cannot change work order status", status_code=403)


    changed = False
    if payload.title and payload.title != wo.title:
        wo.title = payload.title
        changed = True
    if payload.description and payload.description != wo.description:
        wo.description = payload.description
        changed = True
    if payload.status and payload.status != wo.status:
        wo.status = payload.status
        changed = True
    if payload.assigned_to_user_id:
        wo.assigned_to_user_id = UUID(payload.assigned_to_user_id)
        changed = True
    if payload.attachment_url:
        wo.attachment_url = payload.attachment_url
        changed = True
    if changed:
        wo.updated_at = now
        db.add(wo)
    db.add(WorkOrderEvent(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        work_order_id=wo.id,
        actor_user_id=UUID(ctx.user_id),
        type="STATUS_CHANGED" if payload.status else "COMMENT",
        message=payload.message,
        created_at=now,
    ))
    await db.commit()
    return {"ok": True}

@router.get("/{work_order_id}/events")
async def list_work_order_events(
    work_order_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("work_orders:read")),
):
    res = await db.execute(select(WorkOrderEvent).where(
        WorkOrderEvent.tenant_id == UUID(tenant.tenant_id),
        WorkOrderEvent.work_order_id == UUID(work_order_id),
    ).order_by(WorkOrderEvent.created_at.asc()))
    return [
        {
            "id": str(e.id),
            "type": e.type,
            "message": e.message,
            "created_at": e.created_at,
        }
        for e in res.scalars().all()
    ]

@router.delete("/{work_order_id}")
async def delete_work_order(
    work_order_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("work_orders:write")),
):
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles  # Both board roles can perform write operations
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can delete tickets", status_code=403)
        
    res = await db.execute(select(WorkOrder).where(
        WorkOrder.tenant_id == UUID(tenant.tenant_id), 
        WorkOrder.id == UUID(work_order_id)
    ))
    wo = res.scalar_one_or_none()
    if not wo:
        raise AppError(code="NOT_FOUND", message="Work order not found", status_code=404)
        
    # Delete associated events first (if cascading not set, but good practice here)
    await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.work_order_id == wo.id))
    
    await db.delete(wo)
    await db.commit()
    return {"ok": True}
