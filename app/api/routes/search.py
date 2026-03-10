# backend/app/api/routes/search.py
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext, allowed_acls
from app.db.models import Document
from app.services.embeddings import fake_embed


router = APIRouter(prefix="/search", tags=["search"])

class SearchHit(BaseModel):
    document_id: str
    title: str
    score: float
    excerpt: str
    chunk_index: int

@router.get("/documents", response_model=list[SearchHit])
async def search_documents(
    q: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("search:read")),
    limit: int = 10,
):
    # Qdrant removed
    return []

@router.get("/global")
async def search_global(
    q: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("search:read")),
):
    from app.services.search_service import search_global_db
    return await search_global_db(db, tenant.tenant_id, ctx, q)
