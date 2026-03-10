# backend/app/scripts/seed.py
# (replace the file with this version)
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, date
from uuid import uuid4

from sqlalchemy import select
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.db.models import (
    Tenant, User, TenantUser,
    Building, Unit, Occupancy, ResidentProfile,
    Announcement,
    Document,
)
from app.services.storage import Storage

from app.jobs.tasks import enqueue_doc_ingest

TENANT_SLUG = "oakwood-hoa"

async def run():
    # Qdrant removed
    storage = Storage()

    async with SessionLocal() as db:
        # Tenant
        t = (await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))).scalar_one_or_none()
        if not t:
            t = Tenant(id=uuid4(), slug=TENANT_SLUG, name="Oakwood HOA", status="ACTIVE", created_at=datetime.now(timezone.utc))
            db.add(t)
            await db.flush()

        # Users
        board_email = "board@oakwood.test"
        board = (await db.execute(select(User).where(User.email == board_email))).scalar_one_or_none()
        if not board:
            board = User(id=uuid4(), email=board_email, name="Board Admin", password_hash=hash_password("Password123!"), is_platform_admin=False, created_at=datetime.now(timezone.utc))
            db.add(board)
            await db.flush()

        resident_email = "resident@oakwood.test"
        resident = (await db.execute(select(User).where(User.email == resident_email))).scalar_one_or_none()
        if not resident:
            resident = User(id=uuid4(), email=resident_email, name="Resident One", password_hash=hash_password("Password123!"), is_platform_admin=False, created_at=datetime.now(timezone.utc))
            db.add(resident)
            await db.flush()

        # Community structure
        b = Building(id=uuid4(), tenant_id=t.id, name="Building A", created_at=datetime.now(timezone.utc))
        db.add(b)
        await db.flush()

        u = Unit(id=uuid4(), tenant_id=t.id, building_id=b.id, unit_number="A-101", created_at=datetime.now(timezone.utc))
        db.add(u)
        await db.flush()

        # Tenant memberships + unit scoping for resident
        tu_board = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.user_id == board.id))).scalar_one_or_none()
        if not tu_board:
            db.add(TenantUser(id=uuid4(), tenant_id=t.id, user_id=board.id, roles=["BOARD_ADMIN"], unit_id=None, created_at=datetime.now(timezone.utc)))

        tu_res = (await db.execute(select(TenantUser).where(TenantUser.tenant_id == t.id, TenantUser.user_id == resident.id))).scalar_one_or_none()
        if not tu_res:
            db.add(TenantUser(id=uuid4(), tenant_id=t.id, user_id=resident.id, roles=["RESIDENT"], unit_id=u.id, created_at=datetime.now(timezone.utc)))
        else:
            tu_res.unit_id = u.id

        # Resident profile + occupancy
        rp = ResidentProfile(id=uuid4(), tenant_id=t.id, user_id=resident.id, phone=None, is_owner=True, created_at=datetime.now(timezone.utc))
        db.add(rp)

        occ = Occupancy(
            id=uuid4(),
            tenant_id=t.id,
            unit_id=u.id,
            user_id=resident.id,
            type="OWNER",
            start_date=date.today(),
            end_date=None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(occ)

        # Sample announcement
        ann = Announcement(
            id=uuid4(),
            tenant_id=t.id,
            title="Welcome to Oakwood HOA",
            body="Reminder: Quiet hours are 10pm–7am. No overnight street parking.",
            audience="ALL",
            published_at=datetime.now(timezone.utc),
            created_by_user_id=board.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(ann)

        # Sample document + enqueue Qdrant ingest
        content = b"Oakwood HOA CC&Rs: No overnight parking on streets. Quiet hours 10pm-7am.\n"
        key = storage.put(str(t.id), "ccrs.txt", content)
        doc = Document(
            id=uuid4(),
            tenant_id=t.id,
            title="CC&Rs",
            filename="ccrs.txt",
            mime_type="text/plain",
            size_bytes=len(content),
            acl="RESIDENT_VISIBLE",
            storage_key=key,
            created_by_user_id=board.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(doc)
        await db.flush()

        await enqueue_doc_ingest(db=db, tenant_id=str(t.id), document_id=str(doc.id), idempotency_key="seed-ccrs")
        await db.commit()

    print("Seed complete: tenant + board admin + resident + unit + announcement + document ingestion job queued.")

if __name__ == "__main__":
    asyncio.run(run())
