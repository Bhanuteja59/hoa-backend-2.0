# backend/app/core/tenant.py
from __future__ import annotations
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import Tenant

@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    slug: str
    name: str
    community_type: str = "APARTMENTS"

def _extract_slug(host: str) -> str | None:
    host = host.split(":")[0].lower()
    parts = host.split(".")
    if len(parts) < 2:
        return None
    slug = parts[0]
    if slug in {"www", "app"}:
        return None
    return slug


async def resolve_tenant(db, request, override_header=None):
    tenant_id = override_header or request.headers.get("x-tenant-id")

    if tenant_id:
        if tenant_id == "00000000-0000-0000-0000-000000000000":
            return Tenant(id=tenant_id, name="Platform Administration", slug="platform", community_type="APARTMENTS")
            
        res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res.scalar_one_or_none()
    else:
        # Fallback: try slug
        tenant_slug = request.headers.get("x-tenant-slug")
        if not tenant_slug:
            # Try to extract from host (subdomain) if not in headers
            tenant_slug = _extract_slug(request.headers.get("host", ""))
        
        if not tenant_slug:
             raise AppError(code="TENANT_MISSING", message="Tenant not resolved from host or headers", status_code=400)
        
        res = await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        tenant = res.scalar_one_or_none()

    if not tenant:
        raise AppError(code="TENANT_NOT_FOUND", message="Tenant not found/inactive", status_code=404)

    return tenant
