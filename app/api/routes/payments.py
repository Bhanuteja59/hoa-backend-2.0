"""
payments.py — Stripe payment routes for the HOA platform.

Endpoints:
  GET  /payments/config         → returns the Stripe publishable key
  POST /payments/create-intent  → creates a Stripe PaymentIntent
  POST /payments/confirm        → records a successful payment in the ledger
  POST /payments/webhook        → Stripe webhook receiver (raw body)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_auth_ctx, get_db, get_tenant_ctx
from app.api.schemas.payments import PaymentCreate, PaymentResponse
from app.core.rbac import AuthContext
from app.core.tenant import TenantContext
from app.db.models import Payment, TenantUser
from app.services.ledger import recompute_balance_cents
from app.services.stripe_service import stripe_client

router = APIRouter(tags=["payments"])


# ──────────────────────────────────────────────────────────────────────
# Schema helpers
# ──────────────────────────────────────────────────────────────────────

class PaymentConfirm(BaseModel):
    unit_id: str
    amount_cents: int
    payment_id: str


# ──────────────────────────────────────────────────────────────────────
# GET /payments/config  — return publishable key to the frontend
# ──────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_payment_config():
    """Return the Stripe publishable key. No auth required."""
    return {"publishable_key": stripe_client.public_key}


# ──────────────────────────────────────────────────────────────────────
# POST /payments/create-intent  — create a PaymentIntent
# ──────────────────────────────────────────────────────────────────────

@router.post("/create-intent", response_model=PaymentResponse)
async def create_payment_intent(
    payment: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    try:
        # Resolve the resident's unit_id from the database
        tu = (
            await db.execute(
                select(TenantUser).where(
                    TenantUser.tenant_id == uuid.UUID(tenant.tenant_id),
                    TenantUser.user_id == uuid.UUID(ctx.user_id),
                )
            )
        ).scalar_one_or_none()
        unit_id = str(tu.unit_id) if tu and tu.unit_id else None

        metadata = {
            "user_id": str(ctx.user_id),
            "tenant_id": str(tenant.tenant_id),
            "unit_id": unit_id or "",
            "description": payment.description or f"HOA Dues — user {ctx.user_id}",
        }

        intent = stripe_client.create_payment_intent(
            amount_cents=int(payment.amount * 100),
            currency=payment.currency.lower(),
            metadata=metadata,
            description=metadata["description"],
        )

        return PaymentResponse(
            client_secret=intent.client_secret,
            status=intent.status,
            payment_id=intent.id,
            unit_id=unit_id,
        )

    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message or str(exc)}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ──────────────────────────────────────────────────────────────────────
# POST /payments/confirm  — record a confirmed payment in our ledger
# ──────────────────────────────────────────────────────────────────────

@router.post("/confirm")
async def confirm_payment(
    payload: PaymentConfirm,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    try:
        # Verify with Stripe that the PaymentIntent actually succeeded
        intent = stripe_client.retrieve_payment_intent(payload.payment_id)
        if intent.status != "succeeded":
            raise HTTPException(
                status_code=400,
                detail=f"PaymentIntent status is '{intent.status}', not 'succeeded'. "
                       "Payment has not been confirmed by Stripe.",
            )

        # Residents can only post to their own unit
        is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
        if "USER" in ctx.roles and not is_admin_or_board:
            tu = (
                await db.execute(
                    select(TenantUser).where(
                        TenantUser.tenant_id == uuid.UUID(tenant.tenant_id),
                        TenantUser.user_id == uuid.UUID(ctx.user_id),
                    )
                )
            ).scalar_one_or_none()
            user_unit_id = str(tu.unit_id) if tu and tu.unit_id else ""
            if user_unit_id != payload.unit_id:
                raise HTTPException(status_code=403, detail="Unit mismatch — cannot record payment for another unit.")

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
        await db.flush()

        if payload.unit_id:
            await recompute_balance_cents(db, tenant_id=tenant.tenant_id, unit_id=payload.unit_id)

        await db.commit()
        return {"status": "success", "payment_id": str(p.id)}

    except HTTPException:
        await db.rollback()
        raise
    except stripe.StripeError as exc:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message or str(exc)}")
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


# ──────────────────────────────────────────────────────────────────────
# POST /payments/webhook  — receive Stripe webhook events
# ──────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
):
    """
    Stripe will POST signed events to this endpoint after payment events.
    This provides a server-side confirmation path that doesn't rely on the
    browser redirect (more reliable for production).

    Configure in Stripe Dashboard → Webhooks → Add endpoint:
      URL: https://your-backend.com/api/v1/payments/webhook
      Events: payment_intent.succeeded, payment_intent.payment_failed
    """
    raw_body = await request.body()

    # Verify webhook signature (skip if webhook secret is placeholder/unset)
    from app.core.config import settings
    if settings.STRIPE_WEBHOOK_SECRET and not settings.STRIPE_WEBHOOK_SECRET.startswith("whsec_REPLACE"):
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
        try:
            event = stripe_client.construct_webhook_event(raw_body, stripe_signature)
        except stripe.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature")
    else:
        # Dev mode: parse without signature verification
        import json
        event_data = json.loads(raw_body)
        event = stripe.Event.construct_from(event_data, stripe.api_key)

    # ── Handle events ───────────────────────────────────────────────
    if event.type == "payment_intent.succeeded":
        pi: stripe.PaymentIntent = event.data.object
        meta = pi.metadata or {}
        tenant_id = meta.get("tenant_id", "")
        unit_id = meta.get("unit_id", "")
        user_id = meta.get("user_id", "")

        # Only persist if we have the required IDs
        if tenant_id and unit_id and user_id:
            # Prevent duplicate recording (idempotency: check if reference already exists)
            existing = (
                await db.execute(
                    select(Payment).where(Payment.reference == pi.id)
                )
            ).scalar_one_or_none()

            if not existing:
                p = Payment(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(tenant_id),
                    unit_id=uuid.UUID(unit_id),
                    amount_cents=pi.amount_received,
                    method="STRIPE",
                    reference=pi.id,
                    posted_at=datetime.now(timezone.utc),
                    created_by_user_id=uuid.UUID(user_id),
                    created_at=datetime.now(timezone.utc),
                )
                db.add(p)
                await recompute_balance_cents(db, tenant_id=tenant_id, unit_id=unit_id)
                await db.commit()

    elif event.type == "payment_intent.payment_failed":
        pi = event.data.object
        print(f"[Stripe Webhook] Payment FAILED: {pi.id} — {pi.last_payment_error}")

    # Return 200 to acknowledge receipt
    return JSONResponse(content={"received": True})
