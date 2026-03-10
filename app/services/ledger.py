# backend/app/services/ledger.py
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Charge, Payment, LedgerAccount

async def recompute_balance_cents(db: AsyncSession, *, tenant_id: str, unit_id: str) -> int:
    charges_sum = await db.execute(
        select(func.coalesce(func.sum(Charge.amount_cents), 0)).where(
            Charge.tenant_id == tenant_id, Charge.unit_id == unit_id
        )
    )
    payments_sum = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.tenant_id == tenant_id, Payment.unit_id == unit_id
        )
    )
    balance = int(charges_sum.scalar_one()) - int(payments_sum.scalar_one())
    # upsert ledger_accounts cache row
    res = await db.execute(
        select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id, LedgerAccount.unit_id == unit_id)
    )
    acct = res.scalar_one_or_none()
    if acct:
        acct.balance_cents = balance
    else:
        acct = LedgerAccount(tenant_id=tenant_id, unit_id=unit_id, balance_cents=balance)
        db.add(acct)
    return balance
