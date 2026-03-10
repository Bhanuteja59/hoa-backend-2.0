from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.db.models import TenantUser, WorkOrder, Violation, ArcRequest

router = APIRouter(prefix="/stats", tags=["stats"])

@router.get("")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("search:read")), # Using a broad permission readable by all
):
    # Determine if we need to filter by user
    is_resident = "USER" in ctx.roles and "ADMIN" not in ctx.roles
    user_uuid = UUID(ctx.user_id) if is_resident else None

    # Residents Count
    res_users = await db.execute(
        select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == tenant.tenant_id)
    )
    count_users = res_users.scalar()

    # Pending Residents (waiting for confirmation)
    res_pending = await db.execute(
        select(func.count()).select_from(TenantUser).where(
            TenantUser.tenant_id == tenant.tenant_id,
            TenantUser.status == 'pending'
        )
    )
    count_pending = res_pending.scalar()

    # Open Work Orders
    # Assuming 'COMPLETED' and 'CANCELLED' are closed states. Adjust if needed.
    wo_stmt = select(func.count()).select_from(WorkOrder).where(
        WorkOrder.tenant_id == tenant.tenant_id,
        WorkOrder.status.notin_(["COMPLETED", "CANCELLED"])
    )
    if is_resident:
        wo_stmt = wo_stmt.where(WorkOrder.created_by_user_id == user_uuid)
    
    res_wo = await db.execute(wo_stmt)
    count_wo = res_wo.scalar()

    # Open Violations
    vio_stmt = select(func.count()).select_from(Violation).where(
        Violation.tenant_id == tenant.tenant_id,
        Violation.status != "RESOLVED"
    )
    if is_resident:
        vio_stmt = vio_stmt.where(Violation.created_by_user_id == user_uuid)

    res_vio = await db.execute(vio_stmt)
    count_vio = res_vio.scalar()

    # Pending ARC Requests
    arc_stmt = select(func.count()).select_from(ArcRequest).where(
        ArcRequest.tenant_id == tenant.tenant_id,
        ArcRequest.status.notin_(["APPROVED", "REJECTED"])
    )
    if is_resident:
        arc_stmt = arc_stmt.where(ArcRequest.created_by_user_id == user_uuid)

    res_arc = await db.execute(arc_stmt)
    count_arc = res_arc.scalar()

    return {
        "residents_count": count_users,
        "residents_pending": count_pending,
        "open_work_orders": count_wo,
        "open_violations": count_vio,
        "pending_arc": count_arc,
        "community_name": tenant.name
    }
