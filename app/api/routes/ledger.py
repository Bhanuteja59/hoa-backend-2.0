# backend/app/api/routes/ledger.py
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.db.models import Charge, Payment, TenantUser, Notification
from app.services.ledger import recompute_balance_cents
from app.services.notifications import notification_manager

router = APIRouter(prefix="/ledger", tags=["ledger"])

class ChargeIn(BaseModel):
    unit_id: str
    amount_cents: int = Field(gt=0)
    description: str

class PaymentIn(BaseModel):
    unit_id: str
    amount_cents: int = Field(gt=0)
    method: str = "MANUAL"
    reference: str | None = None

def _enforce_unit_scope(db: AsyncSession, tenant_id: str, ctx: AuthContext, unit_id: str):
    # Resident can only read/write their own unit
    # Admin/Manager/Board can act across tenant
    # (async function inlined in endpoints below)
    ...

@router.get("/balance")
async def get_balance(
    unit_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("ledger:read")),
):
    is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
    if "USER" in ctx.roles and not is_admin_or_board:
        tu = (await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id))
        )).scalar_one_or_none()
        if not tu or not tu.unit_id:
            raise AppError(code="UNIT_MISSING", message="Resident unit not configured", status_code=400)
        unit_id = str(tu.unit_id)

    if not unit_id:
        raise AppError(code="UNIT_REQUIRED", message="unit_id required", status_code=400)

    bal = await recompute_balance_cents(db, tenant_id=tenant.tenant_id, unit_id=unit_id)
    await db.commit()
    return {"unit_id": unit_id, "balance_cents": bal}

@router.get("/history")
async def get_history(
    unit_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("ledger:read")),
):
    if "USER" in ctx.roles:
        tu = (await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == UUID(tenant.tenant_id), TenantUser.user_id == UUID(ctx.user_id))
        )).scalar_one_or_none()
        if not tu or not tu.unit_id:
            raise AppError(code="UNIT_MISSING", message="Resident unit not configured", status_code=400)
        unit_id = str(tu.unit_id)

    if not unit_id:
        raise AppError(code="UNIT_REQUIRED", message="unit_id required", status_code=400)

    # Fetch charges and payments
    charges = (await db.execute(
        select(Charge).where(Charge.tenant_id == UUID(tenant.tenant_id), Charge.unit_id == UUID(unit_id))
    )).scalars().all()

    payments = (await db.execute(
        select(Payment).where(Payment.tenant_id == UUID(tenant.tenant_id), Payment.unit_id == UUID(unit_id))
    )).scalars().all()

    transactions = []
    for c in charges:
        transactions.append({
            "id": str(c.id),
            "type": "CHARGE",
            "amount_cents": c.amount_cents,
            "description": c.description,
            "posted_at": c.posted_at
        })
    for p in payments:
        transactions.append({
            "id": str(p.id),
            "type": "PAYMENT",
            "amount_cents": p.amount_cents, # stored positive; UI formats with a leading '-' for payments
            "description": f"Payment: {p.method}" + (f" ({p.reference})" if p.reference else ""),
            "posted_at": p.posted_at
        })

    transactions.sort(key=lambda x: x["posted_at"], reverse=True)
    return transactions

@router.post("/charges")
async def create_charge(
    payload: ChargeIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("ledger:write")),
):
    c = Charge(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        unit_id=UUID(payload.unit_id),
        amount_cents=payload.amount_cents,
        description=payload.description,
        posted_at=datetime.now(timezone.utc),
        created_by_user_id=UUID(ctx.user_id),
        created_at=datetime.now(timezone.utc),
    )
    db.add(c)
    await recompute_balance_cents(db, tenant_id=tenant.tenant_id, unit_id=payload.unit_id)
    
    # Notify unit residents
    res = await db.execute(
        select(TenantUser.user_id).where(
            TenantUser.tenant_id == UUID(tenant.tenant_id),
            TenantUser.unit_id == UUID(payload.unit_id)
        )
    )
    user_ids = res.scalars().all()
    for uid in user_ids:
        n = Notification(
            tenant_id=UUID(tenant.tenant_id),
            user_id=uid,
            title="New Charge Posted",
            message=f"A new charge of ${payload.amount_cents/100:.2f} for '{payload.description}' has been added to your account.",
            type="payment",
            link="/dashboard/dues-ledger"
        )
        db.add(n)
        await notification_manager.notify_user(uid, n.title, n.message, n.type, n.link)

    await db.commit()
    return {"id": str(c.id)}

@router.post("/payments")
async def record_payment(
    payload: PaymentIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("ledger:write")),
):
    p = Payment(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        unit_id=UUID(payload.unit_id),
        amount_cents=payload.amount_cents,
        method=payload.method,
        reference=payload.reference,
        posted_at=datetime.now(timezone.utc),
        created_by_user_id=UUID(ctx.user_id),
        created_at=datetime.now(timezone.utc),
    )
    db.add(p)
    await recompute_balance_cents(db, tenant_id=tenant.tenant_id, unit_id=payload.unit_id)
    
    # Notify unit residents (if recorded by admin)
    if "ADMIN" in ctx.roles or "BOARD" in ctx.roles:
        res = await db.execute(
            select(TenantUser.user_id).where(
                TenantUser.tenant_id == UUID(tenant.tenant_id),
                TenantUser.unit_id == UUID(payload.unit_id)
            )
        )
        user_ids = res.scalars().all()
        for uid in user_ids:
            # Don't notify the person who made the payment if they are a resident? 
            # Actually, usually admin records it so we notify residents.
            n = Notification(
                tenant_id=UUID(tenant.tenant_id),
                user_id=uid,
                title="Payment Recorded",
                message=f"A payment of ${payload.amount_cents/100:.2f} has been credited to your account.",
                type="payment",
                link="/dashboard/dues-ledger"
            )
            db.add(n)
            await notification_manager.notify_user(uid, n.title, n.message, n.type, n.link)

    await db.commit()
    return {"id": str(p.id)}

@router.get("/summary")
async def get_ledger_summary(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("ledger:read")),
):
    """
    Get all units and their balances for the tenant.
    Only accessible to non-USER roles (ADMIN, BOARD, etc).
    """
    # Only Admin/Board should see the full summary
    is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
    if not is_admin_or_board:
         raise AppError(code="FORBIDDEN", message="Only administrative users can access the ledger summary", status_code=403)

    from app.db.models import Unit, LedgerAccount, Building
    
    stmt = (
        select(Unit, LedgerAccount.balance_cents, Building.name)
        .outerjoin(LedgerAccount, Unit.id == LedgerAccount.unit_id)
        .outerjoin(Building, Unit.building_id == Building.id)
        .where(Unit.tenant_id == UUID(tenant.tenant_id))
        .order_by(Building.name, Unit.unit_number)
    )
    
    res = await db.execute(stmt)
    rows = res.all()
    
    return [
        {
            "unit_id": str(u.id),
            "unit_number": u.unit_number,
            "building_name": b_name,
            "balance_cents": bal if bal is not None else 0
        }
        for u, bal, b_name in rows
    ]
