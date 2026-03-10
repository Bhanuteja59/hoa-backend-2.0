# backend/app/api/routes/auth.py
from __future__ import annotations
from app.core.security import verify_password, create_access_token, hash_password, decode_access_token
from app.db.models import User, TenantUser, Tenant, Building, Unit
from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_tenant_ctx, get_auth_ctx, require
from app.core.rbac import AuthContext
from app.core.tenant import TenantContext
from app.core.errors import AppError
from app.core.config import settings

from app.services.email_service import EmailService
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str



class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    roles: list[str]
    user: dict
    tenant_name: str | None = None
    name: str | None = None
    is_platform_admin: bool = False


@router.post("/login")
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    res = await AuthService.login(payload, db)
    return res





@router.get("/me")
async def me(ctx: AuthContext = Depends(get_auth_ctx), tenant: TenantContext = Depends(get_tenant_ctx), db: AsyncSession = Depends(get_db)):
    return await AuthService.get_current_user_profile(ctx, tenant, db)
from pydantic import BaseModel, EmailStr, field_validator
import re

class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    hoa_name: str | None = None
    phone: str | None = None
    role: str = "BOARD_ADMIN"
    tenant_slug: str | None = None
    community_type: str | None = "APARTMENTS" # APARTMENTS, OWN_HOUSES
    registration_number: str | None = None
    account_number: str | None = None
    token: str | None = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one number")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        if v:
            if not v.isdigit():
                raise ValueError("Phone number must contain only numbers")
            if len(v) < 10 or len(v) > 15:
                raise ValueError("Phone number must be between 10 and 15 digits")
        return v

@router.post("/register")
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)):
    return await AuthService.register(payload, db)

@router.post("/regenerate-slug")
async def regenerate_slug(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("tenant:edit")), # We'll need to make sure ADMIN has this permissions or use a broad one
):
    result = await AuthService.regenerate_slug(ctx, tenant, db)
    return {"slug": result}


