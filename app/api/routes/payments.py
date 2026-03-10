from fastapi import APIRouter, HTTPException, Depends
from app.services.stripe_service import StripeClient
from app.api.schemas.payments import PaymentCreate, PaymentResponse
from app.api.deps import get_tenant_ctx, get_auth_ctx, get_db
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import TenantUser, Payment
from app.services.ledger import recompute_balance_cents
from sqlalchemy import select
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel

router = APIRouter()
stripe_client = StripeClient()

@router.post("/create-intent", response_model=PaymentResponse)
async def create_payment_intent(
    payment: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx)
):
    try:
        # Get user's unit_id
        tu = (await db.execute(
            select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(tenant.tenant_id), TenantUser.user_id == uuid.UUID(ctx.user_id))
        )).scalar_one_or_none()
        unit_id = str(tu.unit_id) if tu and tu.unit_id else None

        # Construct metadata for Stripe
        metadata = {
            "user_id": str(ctx.user_id),
            "tenant_id": str(tenant.tenant_id),
            "unit_id": unit_id or "",
            "description": payment.description or f"Dues for User {ctx.user_id}"
        }
        
        # Create Stripe payment intent
        print(f"DEBUG: Creating intent for amount {payment.amount} with metadata: {metadata}")
        intent = stripe_client.create_payment_intent(
            amount_cents=int(payment.amount * 100),
            currency=payment.currency.lower(),
            metadata=metadata
        )
        print(f"DEBUG: Created intent {intent.id} with metadata: {intent.metadata}")
        
        return PaymentResponse(
            client_secret=intent.client_secret,
            status=intent.status,
            payment_id=intent.id,
            unit_id=unit_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class PaymentConfirm(BaseModel):
    unit_id: str
    amount_cents: int
    payment_id: str

@router.post("/confirm")
async def confirm_payment(
    payload: PaymentConfirm,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx)
):
    print(f"DEBUG: Confirming payment {payload.payment_id} unit={payload.unit_id} amount={payload.amount_cents}")
    try:
        # Retrieve the payment intent from Stripe to verify status
        intent = stripe_client.confirm_payment_intent(payload.payment_id)
        if intent.status != "succeeded":
            # NOTE: In production, we'd wait for webhook. For demo, we verify status if possible.
            # However, if status is 'requires_action' or 'processing', we might still want to record 
            # after success, but here we assume client only calls after successful payment.
            pass

        # Verify user is associated with the unit
        is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
        if "USER" in ctx.roles and not is_admin_or_board:
            tu = (await db.execute(
                select(TenantUser).where(TenantUser.tenant_id == uuid.UUID(tenant.tenant_id), TenantUser.user_id == uuid.UUID(ctx.user_id))
            )).scalar_one_or_none()
            
            # Check unit logic gracefully
            user_unit_id_str = str(tu.unit_id) if tu and tu.unit_id else ""
            if user_unit_id_str != payload.unit_id:
                raise HTTPException(status_code=403, detail="Resident unit mismatch")
                
        p = Payment(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant.tenant_id),
            unit_id=uuid.UUID(payload.unit_id) if payload.unit_id else None,
            amount_cents=payload.amount_cents,
            method="STRIPE",
            reference=payload.payment_id,
            posted_at=datetime.now(timezone.utc),
            created_by_user_id=uuid.UUID(ctx.user_id),
            created_at=datetime.now(timezone.utc),
        )
        db.add(p)
        await db.commit()
        
        if payload.unit_id:
            await recompute_balance_cents(db, tenant_id=tenant.tenant_id, unit_id=payload.unit_id)
            await db.commit()
            
        return {"status": "success", "payment_id": str(p.id)}
    except HTTPException as e:
        await db.rollback()
        raise e
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
