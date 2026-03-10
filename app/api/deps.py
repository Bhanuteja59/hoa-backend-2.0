# backend/app/api/deps.py
from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt, JWTError

from app.db.session import AsyncSessionLocal, get_db
from app.core.config import settings
from app.core.errors import AppError
from app.core.tenant import resolve_tenant, TenantContext
from app.core.rbac import AuthContext
from app.core.security import decode_access_token


async def get_tenant_ctx(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_tenant_id: str | None = Header(None)
) -> TenantContext:
    tenant = await resolve_tenant(db, request, x_tenant_id)
    return TenantContext(
        tenant_id=str(tenant.id), 
        slug=tenant.slug, 
        name=tenant.name,
        community_type=getattr(tenant, "community_type", None) or "APARTMENTS"
    )



async def get_auth_ctx(
    request: Request,
    tenant: TenantContext = Depends(get_tenant_ctx),
    db: AsyncSession = Depends(get_db)
) -> AuthContext:


    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise AppError(code="AUTH_REQUIRED", message="Missing bearer token", status_code=401)

    token = auth.split(" ", 1)[1]
    payload = decode_access_token(token)

    # Verify user is from same tenant
    if payload.get("tid") != tenant.tenant_id:
        raise AppError(code="AUTH_TENANT_MISMATCH", message="Wrong tenant", status_code=403)

    user_id = payload.get("sub")
    tenant_id = payload.get("tid")
    is_platform_admin = payload.get("is_platform_admin", False)

    # Normal User Verification
    from sqlalchemy import select
    from app.db.models import TenantUser, User
    
    # 🔍 Security Check: Refresh user profile & verify token still matches current state
    res = await db.execute(
        select(User.password_hash).where(User.id == user_id)
    )
    user_row = res.scalar_one_or_none()
    
    if not user_row:
        raise AppError(code="USER_NOT_FOUND", message="User no longer exists", status_code=401)
        
    # Verify current hash matches token's claim (first 8 chars)
    if payload.get("pv") != user_row[:8]:
        raise AppError(code="AUTH_CREDENTIALS_CHANGED", message="Credentials changed, please log in again", status_code=401)

    # PLATFORM ADMIN BYPASS: Grant them full admin access to any tenant they want
    if is_platform_admin:
        # Since they are platform admin, we don't strictly require tenant membership.
        # However, they must specify a valid tenant ID to proceed if they aren't at the /admin level
        # We can just use the token's tid or the resolved tenant's tid
        return AuthContext(
            user_id=str(user_id),
            tenant_id=str(tenant.tenant_id),
            roles=["ADMIN", "*"],
        )

    # Normal User Verification
    from sqlalchemy import select
    from app.db.models import TenantUser
    try:
        res = await db.execute(
            select(TenantUser.status).where(
                TenantUser.user_id == user_id,
                TenantUser.tenant_id == tenant_id
            )
        )
        tu_status = res.scalar_one_or_none()
        if not tu_status:
            raise AppError(code="USER_NOT_FOUND", message="User not found in this database", status_code=401)
    except AppError:
        raise
    except Exception as e:
        # Log unexpected DB errors but return 401 for safety
        raise AppError(code="DB_ERROR", message="Cannot verify user session", status_code=401)

    roles = list(payload.get("roles", []))
    # Legacy role mapping for backward compatibility
    if "RESIDENT" in roles and "USER" not in roles:
        roles.append("USER")
    
    # Normalize BOARD_ADMIN to ADMIN for proper permission inheritance
    if "BOARD_ADMIN" in roles and "ADMIN" not in roles:
        roles.append("ADMIN")
        
    return AuthContext(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        roles=roles,
    )






def require(permission: str):
    async def dep(ctx: AuthContext = Depends(get_auth_ctx)):
        # Platform admin / ADMIN - full access
        if "ADMIN" in ctx.roles:
            return ctx

        # Board roles: BOARD and BOARD_MEMBER are treated identically
        is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
        if is_board:
            perm_action = permission.split(":")[1] if ":" in permission else ""
            # Board can read everything and write most operational items
            if perm_action == "read":
                return ctx
            if permission in [
                "violations:write", "arc:write", "work_orders:write",
                "docs:write", "announcements:write",
                "units:read", "search:read",
            ]:
                return ctx

        # Regular residents (USER / RESIDENT)
        if "USER" in ctx.roles or "RESIDENT" in ctx.roles:
            if permission in [
                "work_orders:read", "work_orders:write",
                "arc:read", "arc:write",
                "violations:read", "violations:write",
                "docs:read",
                "announcements:read",
                "search:read",
                "units:read",
            ]:
                return ctx

        raise AppError(code="NO_PERMISSION", message="No permission", status_code=403)

    return dep
