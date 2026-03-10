# backend/app/services/ledger.py
from __future__ import annotations

from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Charge, Payment, LedgerAccount

async def recompute_balance_cents(db: AsyncSession, *, tenant_id: str, unit_id: str) -> int:
    tid = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
    uid = UUID(unit_id) if isinstance(unit_id, str) else unit_id

    charges_sum = await db.execute(
        select(func.coalesce(func.sum(Charge.amount_cents), 0)).where(
            Charge.tenant_id == tid, Charge.unit_id == uid
        )
    )
    payments_sum = await db.execute(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.tenant_id == tid, Payment.unit_id == uid
        )
    )
    balance = int(charges_sum.scalar_one()) - int(payments_sum.scalar_one())
    # upsert ledger_accounts cache row
    res = await db.execute(
        select(LedgerAccount).where(LedgerAccount.tenant_id == tid, LedgerAccount.unit_id == uid)
    )
    acct = res.scalar_one_or_none()
    if acct:
        acct.balance_cents = balance
    else:
        acct = LedgerAccount(tenant_id=tid, unit_id=uid, balance_cents=balance)
        db.add(acct)
    return balance
