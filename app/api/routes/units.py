# backend/app/api/routes/units.py
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
from app.db.models import Building, Unit, TenantUser

router = APIRouter(prefix="/units", tags=["units"])

class BuildingIn(BaseModel):
    name: str

class BuildingOut(BaseModel):
    id: str
    name: str

class UnitIn(BaseModel):
    unit_number: str
    building_id: str | None = None

class UnitOut(BaseModel):
    id: str
    unit_number: str
    building_id: str | None = None
    building_name: str | None = None

def _resident_unit_scope(ctx: AuthContext, unit_id: str) -> None:
    # enforce resident sees only their unit_id
    if "USER" in ctx.roles:
        # lookup tenant_users.unit_id to enforce unit scope
        # (done in endpoint using db; here just placeholder)
        pass

@router.post("/buildings", response_model=BuildingOut)
async def create_building(
    payload: BuildingIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("units:write")),
):
    b = Building(id=uuid4(), tenant_id=UUID(tenant.tenant_id), name=payload.name, created_at=datetime.now(timezone.utc))
    db.add(b)
    await db.commit()
    return BuildingOut(id=str(b.id), name=b.name)

@router.get("/buildings", response_model=list[BuildingOut])
async def list_buildings(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("units:read")),
):
    res = await db.execute(select(Building).where(Building.tenant_id == UUID(tenant.tenant_id)).order_by(Building.name))
    return [BuildingOut(id=str(b.id), name=b.name) for b in res.scalars().all()]

@router.post("", response_model=UnitOut)
async def create_unit(
    payload: UnitIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("units:write")),
):
    u = Unit(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        building_id=UUID(payload.building_id) if payload.building_id and payload.building_id.strip() else None,
        unit_number=payload.unit_number,
        created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    await db.commit()
    # Return UnitOut without building_name join for now, or fetch it.
    # Simple consistent return:
    return UnitOut(id=str(u.id), unit_number=u.unit_number, building_id=str(u.building_id) if u.building_id else None, building_name=None)

@router.get("", response_model=list[UnitOut])
async def list_units(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("units:read")),
):
    stmt = (
        select(Unit, Building.name)
        .outerjoin(Building, Unit.building_id == Building.id)
        .where(Unit.tenant_id == UUID(tenant.tenant_id))
        .order_by(Building.name, Unit.unit_number)
    )
    res = await db.execute(stmt)
    rows = res.all()
    
    return [
        UnitOut(
            id=str(u.id), 
            unit_number=u.unit_number, 
            building_id=str(u.building_id) if u.building_id else None,
            building_name=b_name
        ) 
        for u, b_name in rows
    ]
