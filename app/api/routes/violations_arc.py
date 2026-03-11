# backend/app/api/routes/violations_arc.py
from __future__ import annotations

from datetime import datetime, timezone, date
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.db.models import (
    Violation, ViolationNotice, Hearing,
    ArcRequest, ArcReview,
    TenantUser, User, Unit
)

router = APIRouter(prefix="", tags=["violations_arc"])

class ViolationIn(BaseModel):
    unit_id: str | None = None
    type: str
    description: str
    attachment_url: str | None = None

class ViolationPatch(BaseModel):
    type: str | None = None
    description: str | None = None
    attachment_url: str | None = None
    status: str | None = None
    unit_id: str | None = None

class NoticeIn(BaseModel):
    notice_date: date
    due_date: date
    content: str

class HearingIn(BaseModel):
    violation_id: str
    scheduled_at: datetime
    location: str | None = None

class ArcIn(BaseModel):
    unit_id: str | None = None
    title: str
    description: str
    attachment_url: Optional[str] = None
    estimated_start_date: date | None = None
    estimated_end_date: date | None = None

class ArcPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    attachment_url: str | None = None
    status: str | None = None
    estimated_start_date: date | None = None
    estimated_end_date: date | None = None
    actual_end_date: date | None = None

class ArcReviewIn(BaseModel):
    decision: str  # APPROVED|REJECTED
    comments: str | None = None

@router.post("/violations")
async def create_violation(
    payload: ViolationIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:write")),
):
    unit_id = payload.unit_id
    # For violations, residents might not know the offending unit's internal UUID.
    # Default to their own unit_id if missing so the report logs under their account.
    if not unit_id and "USER" in ctx.roles:
        tu = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id)))).scalar_one_or_none()
        if tu and tu.unit_id:
            unit_id = str(tu.unit_id)

    if not unit_id:
        raise AppError(code="UNIT_REQUIRED", message="unit_id required", status_code=400)
    v = Violation(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        unit_id=UUID(unit_id),
        created_by_user_id=UUID(ctx.user_id),
        type=payload.type,
        description=payload.description,
        attachment_url=payload.attachment_url,
        status="OPEN",
        created_at=datetime.now(timezone.utc),
    )
    db.add(v)
    await db.commit()
    return {"id": str(v.id)}

@router.get("/violations")
async def list_violations(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:read")),
):
    stmt = (
        select(Violation, User.name, Unit.unit_number)
        .join(User, Violation.created_by_user_id == User.id)
        .outerjoin(Unit, Violation.unit_id == Unit.id)
        .where(Violation.tenant_id == UUID(tenant.tenant_id))
    )
    # If USER but NOT ADMIN, restrict to own unit/reports
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not is_admin and not is_board:
        tu = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id)))).scalar_one_or_none()
        if tu and tu.unit_id:
            stmt = stmt.where(or_(Violation.created_by_user_id == UUID(ctx.user_id), Violation.unit_id == tu.unit_id))
        else:
            stmt = stmt.where(Violation.created_by_user_id == UUID(ctx.user_id))
    
    res = await db.execute(stmt.order_by(Violation.created_at.desc()).limit(50))
    rows = res.all()
    return [
        {
            "id": str(v.id), 
            "unit_id": str(v.unit_id), 
            "type": v.type, 
            "description": v.description,
            "attachment_url": v.attachment_url,
            "status": v.status,
            "user_name": user_name,
            "unit_number": unit_number,
            "created_at": v.created_at
        } 
        for v, user_name, unit_number in rows
    ]
    
@router.delete("/violations/{violation_id}")
async def delete_violation(
    violation_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:write")),
):
    # ADMIN or Owner can delete
    res = await db.execute(select(Violation).where(
        Violation.tenant_id == UUID(tenant.tenant_id), 
        Violation.id == UUID(violation_id)
    ))
    v = res.scalar_one_or_none()
    if not v:
        raise AppError(code="NOT_FOUND", message="Violation not found", status_code=404)

    if "ADMIN" not in ctx.roles:
        # Check ownership
        if str(v.created_by_user_id) != ctx.user_id:
             raise AppError(code="NO_PERMISSION", message="Cannot delete violation you didn't create", status_code=403)
        
    await db.delete(v)
    await db.commit()
    return {"ok": True}


# ... (skip to ARC delete) ...


@router.delete("/arc-requests/{arc_request_id}")
async def delete_arc_request(
    arc_request_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("arc:write")),
):
    res = await db.execute(select(ArcRequest).where(
        ArcRequest.tenant_id == UUID(tenant.tenant_id), 
        ArcRequest.id == UUID(arc_request_id)
    ))
    r = res.scalar_one_or_none()
    if not r:
        raise AppError(code="NOT_FOUND", message="ARC request not found", status_code=404)
    
    if "ADMIN" not in ctx.roles:
        if str(r.created_by_user_id) != ctx.user_id:
             raise AppError(code="NO_PERMISSION", message="Cannot delete ARC request you didn't create", status_code=403)
        
    await db.delete(r)
    await db.commit()
    return {"ok": True}

@router.post("/violations/{violation_id}/notices")
async def create_notice(
    violation_id: str,
    payload: NoticeIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:write")),
):
    n = ViolationNotice(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        violation_id=UUID(violation_id),
        notice_date=payload.notice_date,
        due_date=payload.due_date,
        content=payload.content,
        created_at=datetime.now(timezone.utc),
    )
    db.add(n)
    await db.commit()
    return {"id": str(n.id)}

@router.post("/hearings")
async def schedule_hearing(
    payload: HearingIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:write")),
):
    h = Hearing(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        violation_id=UUID(payload.violation_id),
        scheduled_at=payload.scheduled_at,
        location=payload.location,
        outcome=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(h)
    await db.commit()
    return {"id": str(h.id)}

@router.post("/arc-requests")
async def create_arc_request(
    payload: ArcIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("arc:write")),
):
    unit_id = payload.unit_id
    
    # Check if user has privileged access (ADMIN or BOARD)
    has_privileged_access = "ADMIN" in ctx.roles or "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles

    if not has_privileged_access:
        # Regular residents can only create for their own unit
        if "USER" in ctx.roles:
            tu = (await db.execute(select(TenantUser).where(
                TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id)
            ))).scalar_one_or_none()
            if not tu or not tu.unit_id:
                raise AppError(code="UNIT_MISSING", message="Resident unit not configured", status_code=400)
            unit_id = str(tu.unit_id)

    if not unit_id:
        raise AppError(code="UNIT_REQUIRED", message="unit_id required", status_code=400)

    r = ArcRequest(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        unit_id=UUID(unit_id),
        created_by_user_id=UUID(ctx.user_id),
        title=payload.title,
        description=payload.description,
        attachment_url=payload.attachment_url,
        estimated_start_date=payload.estimated_start_date,
        estimated_end_date=payload.estimated_end_date,
        status="SUBMITTED",
        created_at=datetime.now(timezone.utc),
    )
    db.add(r)
    await db.commit()
    return {"id": str(r.id)}

@router.get("/arc-requests")
async def list_arc_requests(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("arc:read")),
):
    stmt = (
        select(ArcRequest, User.name, Unit.unit_number)
        .join(User, ArcRequest.created_by_user_id == User.id)
        .join(Unit, ArcRequest.unit_id == Unit.id)
        .where(ArcRequest.tenant_id == UUID(tenant.tenant_id))
    )
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not is_admin and not is_board:
        tu = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id)))).scalar_one_or_none()
        if tu and tu.unit_id:
            stmt = stmt.where(or_(ArcRequest.created_by_user_id == UUID(ctx.user_id), ArcRequest.unit_id == tu.unit_id))
        else:
            stmt = stmt.where(ArcRequest.created_by_user_id == UUID(ctx.user_id))
    
    res = await db.execute(stmt.order_by(ArcRequest.created_at.desc()).limit(50))
    rows = res.all()
    return [
        {
            "id": str(a.id), 
            "unit_id": str(a.unit_id), 
            "title": a.title, 
            "description": a.description,
            "attachment_url": a.attachment_url,
            "status": a.status,
            "estimated_start_date": a.estimated_start_date,
            "estimated_end_date": a.estimated_end_date,
            "actual_end_date": a.actual_end_date,
            "user_name": user_name,
            "unit_number": unit_number,
            "created_at": a.created_at
        } 
        for a, user_name, unit_number in rows
    ]

@router.post("/arc-requests/{arc_request_id}/reviews")
async def review_arc_request(
    arc_request_id: str,
    payload: ArcReviewIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("arc:write")),
):
    rv = ArcReview(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        arc_request_id=UUID(arc_request_id),
        reviewer_user_id=UUID(ctx.user_id),
        decision=payload.decision,
        comments=payload.comments,
        created_at=datetime.now(timezone.utc),
    )
    db.add(rv)
    # Update parent status (simple policy)
    res = await db.execute(select(ArcRequest).where(
        ArcRequest.tenant_id == UUID(tenant.tenant_id), ArcRequest.id == UUID(arc_request_id)
    ))
    req = res.scalar_one_or_none()
    if req:
        new_status = "APPROVED" if payload.decision == "APPROVED" else "REJECTED"
        req.status = new_status
        # Logic: If decided, we might set actual_end_date to now if not set?
        # User requested: "date need to set automaitcally end date when it is updated"
        # Since this is "review_arc_request", it updates the status.
        if new_status in ("APPROVED", "REJECTED") and not req.actual_end_date:
            req.actual_end_date = datetime.now(timezone.utc).date()
            
    await db.commit()
    return {"id": str(rv.id)}

class StatusUpdate(BaseModel):
    status: str

@router.patch("/violations/{violation_id}")
async def update_violation(
    violation_id: str,
    payload: ViolationPatch,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("violations:write")),
):
    res = await db.execute(select(Violation).where(
        Violation.tenant_id == UUID(tenant.tenant_id), Violation.id == UUID(violation_id)
    ))
    v = res.scalar_one_or_none()
    if not v:
        raise AppError(code="NOT_FOUND", message="Violation not found", status_code=404)
    
    # Permission Check
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    is_owner = str(v.created_by_user_id) == ctx.user_id

    if not (is_admin or is_board or is_owner):
        raise AppError(code="NO_PERMISSION", message="No permission to update this violation", status_code=403)

    # Restrict Status Update
    if payload.status and payload.status != v.status:
        if not (is_admin or is_board):
             raise AppError(code="NO_PERMISSION", message="Only Admin/Board can change status", status_code=403)
        v.status = payload.status

    if payload.description is not None:
        v.description = payload.description
    if payload.type is not None:
        v.type = payload.type
    if payload.attachment_url is not None:
        v.attachment_url = payload.attachment_url
    if payload.unit_id is not None and (is_admin or is_board): # Only admin/board can reassign unit
        v.unit_id = UUID(payload.unit_id)

    await db.commit()
    return {"ok": True}

@router.patch("/arc-requests/{arc_request_id}")
async def update_arc_request(
    arc_request_id: str,
    payload: ArcPatch,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("arc:write")),
):
    res = await db.execute(select(ArcRequest).where(
        ArcRequest.tenant_id == UUID(tenant.tenant_id), ArcRequest.id == UUID(arc_request_id)
    ))
    r = res.scalar_one_or_none()
    if not r:
        raise AppError(code="NOT_FOUND", message="ARC request not found", status_code=404)
    
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    is_owner = str(r.created_by_user_id) == ctx.user_id

    if not (is_admin or is_board or is_owner):
        raise AppError(code="NO_PERMISSION", message="No permission", status_code=403)

    if payload.status and payload.status != r.status:
        if not (is_admin or is_board):
            raise AppError(code="NO_PERMISSION", message="Only Admin/Board can change status", status_code=403)
        r.status = payload.status
        # If status changed to a "closed" state, auto-set actual_end_date
        if r.status in ("APPROVED", "REJECTED", "COMPLETED", "RESOLVED") and not payload.actual_end_date and not r.actual_end_date:
            r.actual_end_date = datetime.now(timezone.utc).date()

    if payload.title is not None:
        r.title = payload.title
    if payload.description is not None:
        r.description = payload.description
    if payload.attachment_url is not None:
        r.attachment_url = payload.attachment_url
    
    # Update Dates
    if payload.estimated_start_date is not None:
        r.estimated_start_date = payload.estimated_start_date
    if payload.estimated_end_date is not None:
        r.estimated_end_date = payload.estimated_end_date
    if payload.actual_end_date is not None:
        r.actual_end_date = payload.actual_end_date

    await db.commit()
    return {"ok": True}


