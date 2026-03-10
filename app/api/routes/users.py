from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, field_validator
import re
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_tenant_ctx, require, get_auth_ctx
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])

class UserOut(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str
    roles: list[str] = []  # All roles the user holds in this tenant
    status: str | None = "active"
    unit_id: str | None = None
    unit: str | None = None
    unit_number: str | None = None
    building_name: str | None = None
    address: str | None = None
    phone: str | None = None
    community_type: str | None = "APARTMENTS"
    registration_number: str | None = None
    account_number: str | None = None
    # Privacy Settings
    privacy_show_name: bool = True
    privacy_show_email: bool = False
    privacy_show_phone: bool = False
    privacy_show_address: bool = False
    directory_visibility: str = "RESIDENTS"
    created_at: datetime



class UserCreateIn(BaseModel):
    name: str
    email: EmailStr
    password: str | None = None
    phone: str
    role: str = "USER"
    unit: str | None = None  # Format: "Block Number" or just "Number"

    address: str
    community_type: str | None = None # Optional, updates Tenant if provided
    community_code: str | None = None # Optional, for Super Admin to target specific tenant
    registration_number: str | None = None # 6-digit invitation code
    account_number: str | None = None # 12-digit permanent ID

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str | None) -> str | None:
        if v is None:
            return v
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
    def validate_phone(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("Phone number must contain only numbers")
        if len(v) < 10 or len(v) > 15:
            raise ValueError("Phone number must be between 10 and 15 digits")
        return v

    @field_validator("account_number")
    @classmethod
    def validate_account_number(cls, v: str | None) -> str | None:
        if v and (not v.isdigit() or len(v) != 12):
            raise ValueError("Account number must be exactly 12 digits")
        return v

    @field_validator("registration_number")
    @classmethod
    def validate_registration_number(cls, v: str | None) -> str | None:
        if v and (not v.isdigit() or len(v) != 6):
            raise ValueError("Registration number must be exactly 6 digits")
        return v


class UserUpdateIn(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    role: str | None = None
    status: str | None = None
    unit: str | None = None  # Format: "Block Number" or just "Number"
    address: str | None = None
    community_type: str | None = None # Optional, updates Tenant directly
    # Privacy Settings
    privacy_show_name: bool | None = None
    privacy_show_email: bool | None = None
    privacy_show_phone: bool | None = None
    privacy_show_address: bool | None = None
    directory_visibility: str | None = None
    registration_number: str | None = None
    account_number: str | None = None

    @field_validator("account_number")
    @classmethod
    def validate_account_number(cls, v: str | None) -> str | None:
        if v and (not v.isdigit() or len(v) != 12):
            raise ValueError("Account number must be exactly 12 digits")
        return v

    @field_validator("registration_number")
    @classmethod
    def validate_registration_number(cls, v: str | None) -> str | None:
        if v and (not v.isdigit() or len(v) != 6):
            raise ValueError("Registration number must be exactly 6 digits")
        return v


@router.get("", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.list_users(ctx, tenant, db, UserOut)


@router.post("", response_model=UserOut)
async def create_user(
    payload: UserCreateIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("tenant:edit")), # Admin only
):
    return await UserService.create_user(payload, ctx, tenant, db, UserOut)


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    payload: UserUpdateIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx), 
):
    return await UserService.update_user(user_id, payload, ctx, tenant, db)


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("tenant:edit")), # Admin only
):
    return await UserService.delete_user(user_id, ctx, tenant, db)


class UserPasswordUpdate(BaseModel):
    current_password: str
    new_password: str

@router.put("/me/password")
async def update_password(
    payload: UserPasswordUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.update_password(payload, ctx, db)


# ------------------------------------------------------------------------
# User Contacts (Emergency / Additional)
# ------------------------------------------------------------------------

class ContactOut(BaseModel):
    id: str
    user_id: str
    name: str
    relation: str
    email: str | None = None
    phone: str | None = None
    is_primary: bool
    address: dict | None = None
    created_at: datetime

class ContactIn(BaseModel):
    name: str
    relation: str
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False
    address: dict | None = None


@router.get("/me/contacts", response_model=list[ContactOut])
async def list_my_contacts(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.list_my_contacts(ctx, tenant, db, ContactOut)


@router.post("/me/contacts", response_model=ContactOut)
async def create_my_contact(
    payload: ContactIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.create_my_contact(payload, ctx, tenant, db, ContactOut)


@router.put("/me/contacts/{contact_id}", response_model=ContactOut)
async def update_my_contact(
    contact_id: str,
    payload: ContactIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.update_my_contact(contact_id, payload, ctx, tenant, db, ContactOut)


@router.delete("/me/contacts/{contact_id}")
async def delete_my_contact(
    contact_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    return await UserService.delete_my_contact(contact_id, ctx, tenant, db)
