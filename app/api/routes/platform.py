from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_auth_ctx, get_platform_auth_ctx
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.core.security import hash_password, decode_access_token
from app.db.models import (
    Tenant, User, TenantUser, Building, Unit, Document, WorkOrder, Violation, 
    Hearing, ViolationNotice, ArcRequest, Announcement, Payment, Charge, Invoice, 
    LedgerAccount, ResidentProfile, Occupancy, ArcReview, WorkOrderEvent,
    DocumentEmbedding, DocumentFolder, UserContact, AnalyticsEvent
)
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID
import uuid

router = APIRouter(tags=["platform"])

class TenantOut(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    community_type: str | None = None
    created_at: datetime

class TenantDetailOut(TenantOut):
    pass

class TenantUserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    status: str
    created_at: datetime

class TenantCreate(BaseModel):
    name: str # Community Name
    slug: str | None = None
    admin_email: str
    admin_name: str
    admin_password: str
    community_type: str | None = "APARTMENTS" # APARTMENTS, OWN_HOUSES

class TenantUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    status: str | None = None

async def require_platform_admin(
    db: AsyncSession = Depends(get_db),
    ctx: AuthContext = Depends(get_platform_auth_ctx)
) -> User:
    res = await db.execute(select(User).where(User.id == UUID(ctx.user_id)))
    user = res.scalar_one_or_none()
    if not user or not user.is_platform_admin:
        raise AppError(code="FORBIDDEN", message="Platform admin access required", status_code=403)
    return user

# Admin panel path prefixes that should NEVER count in public/user analytics
ADMIN_PATH_PREFIXES = ("/admin",)

def _is_admin_path(path: str | None) -> bool:
    """Return True if the path belongs to the super-admin panel."""
    if not path:
        return False
    return any(path.startswith(prefix) for prefix in ADMIN_PATH_PREFIXES)

# --- Analytics Schemas ---
class AnalyticsTrackIn(BaseModel):
    event_type: str
    path: str | None = None
    referrer: str | None = None
    user_agent: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None  # Browser session fingerprint for anonymous dedup

@router.post("/analytics/track")
async def track_analytics(
    payload: AnalyticsTrackIn,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Publicly accessible endpoint to record a page view.
    Super-admin visits and admin-panel paths are silently ignored.
    No authentication required — works for anonymous and logged-in users.
    """
    # Silently drop admin-panel paths
    if _is_admin_path(payload.path):
        return {"ok": True}

    # Try to get user_id from token if present — but never fail if missing
    user_id = None
    is_platform_admin = False
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            token = auth.split(" ", 1)[1]
            if token and token.strip():
                p = decode_access_token(token)
                user_id = p.get("sub")
                if user_id:
                    res = await db.execute(
                        select(User.is_platform_admin).where(User.id == UUID(user_id))
                    )
                    is_platform_admin = bool(res.scalar())
        except:
            pass

    # Drop platform admin visits
    if is_platform_admin:
        return {"ok": True}

    ip = request.client.host if request.client else "unknown"

    # Simple location guess (production should use real GeoIP)
    location = "Local Network"
    if ip not in ("127.0.0.1", "::1", "localhost"):
        location = "Remote Visitor"

    event = AnalyticsEvent(
        id=uuid.uuid4(),
        event_type=payload.event_type,
        path=payload.path,
        referrer=payload.referrer,
        user_agent=payload.user_agent or request.headers.get("user-agent"),
        ip_address=ip,
        location=location,
        tenant_id=UUID(payload.tenant_id) if payload.tenant_id else None,
        user_id=UUID(user_id) if user_id else None,
        session_id=payload.session_id,
        created_at=datetime.utcnow()
    )
    db.add(event)
    await db.commit()
    return {"ok": True}

@router.get("/stats/realtime")
async def get_realtime_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """
    Returns real visitors active in the last 5 minutes.
    Three-tier unique visitor deduplication: user_id > session_id > ip_address.
    Excludes platform admin visits and admin-panel paths.
    """
    from sqlalchemy import func, cast, String, not_, or_, text
    from datetime import timedelta

    now = datetime.utcnow()
    five_min_ago = now - timedelta(minutes=5)
    thirty_min_ago = now - timedelta(minutes=30)

    # Helper: unique visitor key — prefers user_id, then session_id, then ip
    def visitor_key():
        return func.coalesce(
            cast(AnalyticsEvent.user_id, String),
            AnalyticsEvent.session_id,
            AnalyticsEvent.ip_address,
        )

    def apply_realtime_filters(query):
        """Exclude admin paths and platform-admin user visits."""
        return (
            query
            .outerjoin(User, AnalyticsEvent.user_id == User.id)
            .where(AnalyticsEvent.created_at >= thirty_min_ago)
            .where(AnalyticsEvent.path.isnot(None))
            .where(not_(func.lower(AnalyticsEvent.path).like("/admin%")))
            .where(or_(User.id.is_(None), User.is_platform_admin == False))
        )

    # ── 1. Unique visitors in last 5 min (active right now) ──
    res_active = await db.execute(
        apply_realtime_filters(
            select(func.count(func.distinct(visitor_key())))
        ).where(AnalyticsEvent.created_at >= five_min_ago)
    )
    active_users = res_active.scalar() or 0

    # ── 2. 30-minute per-minute rolling window ──
    res_timeline = await db.execute(
        apply_realtime_filters(
            select(
                func.date_trunc("minute", AnalyticsEvent.created_at).label("minute"),
                func.count(func.distinct(visitor_key())).label("cnt")
            )
        )
        .group_by(text("minute"))
        .order_by(text("minute"))
    )
    minute_buckets = {row.minute: row.cnt for row in res_timeline}

    rolling = []
    for i in range(30):
        t = (thirty_min_ago + timedelta(minutes=i)).replace(second=0, microsecond=0)
        rolling.append({
            "time": t.strftime("%H:%M"),
            "visitors": minute_buckets.get(t, 0)
        })

    # ── 3. Top pages by unique visitors in last 30 min ──
    res_pages = await db.execute(
        apply_realtime_filters(
            select(
                AnalyticsEvent.path,
                func.count(AnalyticsEvent.id).label("total_views"),
                func.count(func.distinct(visitor_key())).label("unique_visitors")
            )
        )
        .group_by(AnalyticsEvent.path)
        .order_by(func.count(func.distinct(visitor_key())).desc())
        .limit(15)
    )
    top_pages = [
        {"path": row[0] or "/", "views": row[1], "unique_visitors": row[2]}
        for row in res_pages
    ]

    # ── 4. Live event feed: last 25 page views ──
    res_feed = await db.execute(
        select(
            AnalyticsEvent.id,
            AnalyticsEvent.path,
            AnalyticsEvent.ip_address,
            AnalyticsEvent.user_agent,
            AnalyticsEvent.created_at,
            AnalyticsEvent.user_id,
            AnalyticsEvent.session_id,
            User.name.label("user_name"),
        )
        .outerjoin(User, AnalyticsEvent.user_id == User.id)
        .where(AnalyticsEvent.created_at >= thirty_min_ago)
        .where(AnalyticsEvent.path.isnot(None))
        .where(not_(func.lower(AnalyticsEvent.path).like("/admin%")))
        .where(or_(User.id.is_(None), User.is_platform_admin == False))
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(25)
    )
    live_feed = []
    for row in res_feed:
        ua = (row.user_agent or "").lower()
        if "mobile" in ua or "android" in ua or "iphone" in ua:
            device = "mobile"
        elif "tablet" in ua or "ipad" in ua:
            device = "tablet"
        else:
            device = "desktop"
        delta = now - row.created_at.replace(tzinfo=None)
        live_feed.append({
            "id": str(row.id),
            "path": row.path or "/",
            "ip": row.ip_address,
            "device": device,
            "user_name": row.user_name,
            "session_id": row.session_id,
            "is_authenticated": row.user_id is not None,
            "seconds_ago": int(delta.total_seconds()),
            "timestamp": row.created_at.isoformat(),
        })

    # ── 5. Community breakdown: active tenants in last 5 min ──
    res_communities = await db.execute(
        select(
            AnalyticsEvent.tenant_id,
            Tenant.name.label("tenant_name"),
            func.count(func.distinct(visitor_key())).label("active_count"),
        )
        .outerjoin(User, AnalyticsEvent.user_id == User.id)
        .outerjoin(Tenant, AnalyticsEvent.tenant_id == Tenant.id)
        .where(AnalyticsEvent.created_at >= five_min_ago)
        .where(AnalyticsEvent.path.isnot(None))
        .where(not_(func.lower(AnalyticsEvent.path).like("/admin%")))
        .where(or_(User.id.is_(None), User.is_platform_admin == False))
        .where(AnalyticsEvent.tenant_id.isnot(None))
        .group_by(AnalyticsEvent.tenant_id, Tenant.name)
        .order_by(func.count(func.distinct(visitor_key())).desc())
        .limit(5)
    )
    community_breakdown = [
        {
            "tenant_id": str(row.tenant_id) if row.tenant_id else None,
            "name": row.tenant_name or "Unknown Community",
            "active": row.active_count,
        }
        for row in res_communities
    ]

    return {
        "active_users": active_users,
        "rolling_30min": rolling,
        "top_pages_now": top_pages,
        "live_feed": live_feed,
        "community_breakdown": community_breakdown,
        "as_of": now.isoformat()
    }

@router.get("/stats/analytics")
async def get_analytics_stats(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    from sqlalchemy import func, cast, Date, not_
    from datetime import timedelta
    
    dt_start = datetime.utcnow() - timedelta(days=days-1)

    # ── Base filter: exclude admin-panel paths AND platform-admin user visits ──
    # We join AnalyticsEvent with User so we can check is_platform_admin.
    # Events with no user_id are anonymous public visitors — always counted.
    # ── Base filter logic (optimized) ──
    from sqlalchemy import or_
    def apply_base_filters(query):
        return (
            query
            .outerjoin(User, AnalyticsEvent.user_id == User.id)
            .where(AnalyticsEvent.created_at >= dt_start)
            .where(not_(func.lower(AnalyticsEvent.path).like("/admin%")))
            .where(or_(User.id == None, User.is_platform_admin == False))
        )

    # ── Timeline: unique visitor count per day ──
    from sqlalchemy import String, cast as sa_cast
    def visitor_key():
        return func.coalesce(
            sa_cast(AnalyticsEvent.user_id, String),
            AnalyticsEvent.session_id,
            AnalyticsEvent.ip_address,
        )

    res = await db.execute(
        apply_base_filters(
            select(
                cast(AnalyticsEvent.created_at, Date).label("day"),
                func.count(func.distinct(visitor_key())).label("count")
            )
        )
        .group_by(cast(AnalyticsEvent.created_at, Date))
        .order_by(cast(AnalyticsEvent.created_at, Date))
    )
    traffic = {str(row.day): row.count for row in res}
    
    timeline = []
    for i in range(days):
        day = (dt_start + timedelta(days=i)).date()
        day_str = str(day)
        timeline.append({
            "date": day_str,
            "visitors": traffic.get(day_str, 0)
        })

    # ── Location Breakdown (user visits only) ──
    res_loc = await db.execute(
        apply_base_filters(
            select(AnalyticsEvent.location, func.count(AnalyticsEvent.id))
        )
        .group_by(AnalyticsEvent.location)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(5)
    )
    locations = [{"name": row[0] or "Unknown", "value": row[1]} for row in res_loc]

    # ── Top Pages: only real-user pages, counting each user once per path ──
    res_pages = await db.execute(
        apply_base_filters(
            select(
                AnalyticsEvent.path,
                func.count(func.distinct(visitor_key())).label("unique_visitors")
            )
        )
        .group_by(AnalyticsEvent.path)
        .order_by(func.count(func.distinct(visitor_key())).desc())
        .limit(10)
    )
    top_pages = [{"path": row[0] or "/", "count": row[1]} for row in res_pages]


    return {
        "timeline": timeline,
        "locations": locations,
        "top_pages": top_pages,
        "total_visitors": sum(traffic.values())
    }

@router.get("/stats/overview")
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    from sqlalchemy import func
    
    # Tenants
    t_res = await db.execute(select(func.count(Tenant.id)))
    total_communities = t_res.scalar() or 0
    
    # Active Tenants
    a_res = await db.execute(select(func.count(Tenant.id)).where(Tenant.status == "ACTIVE"))
    active_communities = a_res.scalar() or 0
    
    # Total Users
    u_res = await db.execute(select(func.count(User.id)))
    total_users = u_res.scalar() or 0
    
    # Total Buildings
    b_res = await db.execute(select(func.count(Building.id)))
    total_buildings = b_res.scalar() or 0
    
    return {
        "total_communities": total_communities,
        "active_communities": active_communities,
        "total_users": total_users,
        "total_buildings": total_buildings,
    }

@router.get("/stats/timeline")
async def get_platform_timeline(
    start_date: str | None = None,
    end_date: str | None = None,
    granularity: str = "day",  # day | week | month
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """
    Returns real activity counts (work orders, violations, ARC requests, new users)
    grouped by date within `start_date`..`end_date`.
    """
    from sqlalchemy import func, cast, Date, text
    from datetime import datetime, timedelta

    # Parse date bounds — default to last 30 days
    try:
        dt_end = datetime.fromisoformat(end_date) if end_date else datetime.utcnow()
        dt_start = datetime.fromisoformat(start_date) if start_date else dt_end - timedelta(days=29)
    except ValueError:
        dt_end = datetime.utcnow()
        dt_start = dt_end - timedelta(days=29)

    # Clamp range to maximum 366 days to avoid very heavy queries
    if (dt_end - dt_start).days > 366:
        dt_start = dt_end - timedelta(days=366)

    # Helper: group counts by cast(created_at, Date)
    async def count_by_date(model, date_col="created_at"):
        col = getattr(model, date_col)
        res = await db.execute(
            select(
                cast(col, Date).label("day"),
                func.count(model.id).label("cnt")
            ).where(
                col >= dt_start,
                col <= dt_end
            ).group_by(cast(col, Date)).order_by(cast(col, Date))
        )
        return {str(row.day): row.cnt for row in res}

    # Run all aggregations in parallel-ish (sequentially, all fast single-pass)
    workorder_map = await count_by_date(WorkOrder)
    violation_map = await count_by_date(Violation)
    arc_map = await count_by_date(ArcRequest)
    newuser_map = await count_by_date(TenantUser)  # when resident joined a tenant

    # Build a complete day-by-day series filling zeros for missing days
    days_delta = (dt_end.date() - dt_start.date()).days + 1
    timeline = []
    for i in range(days_delta):
        day = dt_start.date() + timedelta(days=i)
        day_str = str(day)

        label = day.strftime("%b %d")
        if granularity == "month":
            label = day.strftime("%b %Y")
        elif granularity == "week":
            label = f"W{day.isocalendar()[1]} {day.year}"

        timeline.append({
            "date": day_str,
            "label": label,
            "workOrders": workorder_map.get(day_str, 0),
            "violations": violation_map.get(day_str, 0),
            "arcRequests": arc_map.get(day_str, 0),
            "newUsers": newuser_map.get(day_str, 0),
        })

    # If weekly/monthly granularity, collapse the daily buckets
    if granularity in ("week", "month"):
        collapsed: dict[str, dict] = {}
        for entry in timeline:
            key = entry["label"]
            if key not in collapsed:
                collapsed[key] = {"date": entry["date"], "label": key, "workOrders": 0, "violations": 0, "arcRequests": 0, "newUsers": 0}
            collapsed[key]["workOrders"] += entry["workOrders"]
            collapsed[key]["violations"] += entry["violations"]
            collapsed[key]["arcRequests"] += entry["arcRequests"]
            collapsed[key]["newUsers"] += entry["newUsers"]
        timeline = list(collapsed.values())

    return {
        "timeline": timeline,
        "meta": {
            "start_date": str(dt_start.date()),
            "end_date": str(dt_end.date()),
            "granularity": granularity,
            "total_points": len(timeline),
        }
    }

@router.get("/users/all")
async def list_all_users(
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Return all users across the platform with their community memberships."""
    from sqlalchemy import func, or_

    # Get all tenant_user rows joined with user and tenant info
    q = (
        select(
            User.id,
            User.name,
            User.email,
            User.is_platform_admin,
            User.created_at,
            Tenant.id.label("tenant_id"),
            Tenant.name.label("tenant_name"),
            Tenant.slug.label("tenant_slug"),
            TenantUser.roles,
            TenantUser.status.label("tu_status"),
        )
        .outerjoin(TenantUser, TenantUser.user_id == User.id)
        .outerjoin(Tenant, Tenant.id == TenantUser.tenant_id)
        .order_by(User.created_at.desc())
    )
    if search:
        q = q.where(or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%")))

    res = await db.execute(q)
    rows = res.all()

    # Group rows by user (a user can have multiple tenant rows)
    from collections import defaultdict
    users_map: dict = {}
    for row in rows:
        uid = str(row.id)
        if uid not in users_map:
            users_map[uid] = {
                "id": uid,
                "name": row.name,
                "email": row.email,
                "is_platform_admin": row.is_platform_admin,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "communities": [],
            }
        if row.tenant_id:
            users_map[uid]["communities"].append({
                "id": str(row.tenant_id),
                "name": row.tenant_name,
                "slug": row.tenant_slug,
                "roles": row.roles or [],
                "status": row.tu_status,
            })
    return list(users_map.values())


@router.get("/buildings/all")
async def list_all_buildings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Return all buildings across all tenants."""
    from sqlalchemy import func

    res = await db.execute(
        select(
            Building.id,
            Building.name,
            Building.tenant_id,
            Building.created_at,
            Tenant.name.label("tenant_name"),
            Tenant.slug.label("tenant_slug"),
            func.count(Unit.id).label("unit_count"),
        )
        .join(Tenant, Tenant.id == Building.tenant_id)
        .outerjoin(Unit, Unit.building_id == Building.id)
        .group_by(Building.id, Building.name, Building.tenant_id, Building.created_at, Tenant.name, Tenant.slug)
        .order_by(Building.created_at.desc())
    )
    rows = res.all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "tenant_id": str(r.tenant_id),
            "tenant_name": r.tenant_name,
            "tenant_slug": r.tenant_slug,
            "unit_count": r.unit_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

# ──────────────────────────────────────────────
#  Admin Profile
# ──────────────────────────────────────────────

class AdminProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None

class AdminPasswordChange(BaseModel):
    current_password: str
    new_password: str

@router.get("/profile")
async def get_admin_profile(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Return the authenticated super-admin's profile."""
    from sqlalchemy import func
    # Count things they "did": work orders created, tenants provisioned
    tenant_count_res = await db.execute(select(func.count(Tenant.id)))
    total_tenants = tenant_count_res.scalar() or 0

    user_count_res = await db.execute(select(func.count(User.id)))
    total_users = user_count_res.scalar() or 0

    return {
        "id": str(admin.id),
        "name": admin.name,
        "email": admin.email,
        "is_platform_admin": admin.is_platform_admin,
        "created_at": admin.created_at.isoformat() if admin.created_at else None,
        "platform_stats": {
            "total_communities": total_tenants,
            "total_users": total_users,
        }
    }


@router.patch("/profile")
async def update_admin_profile(
    payload: AdminProfileUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Update the super-admin's name and/or email."""
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()
    if payload.email is not None:
        # Check uniqueness
        existing = await db.execute(select(User).where(User.email == payload.email, User.id != admin.id))
        if existing.scalar_one_or_none():
            raise AppError(code="EMAIL_TAKEN", message="Email already in use", status_code=409)
        updates["email"] = payload.email.strip().lower()

    if updates:
        await db.execute(update(User).where(User.id == admin.id).values(**updates))
        await db.commit()

    # Return fresh data
    res = await db.execute(select(User).where(User.id == admin.id))
    updated = res.scalar_one()
    return {"id": str(updated.id), "name": updated.name, "email": updated.email}


@router.post("/profile/change-password")
async def change_admin_password(
    payload: AdminPasswordChange,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Change the super-admin's password with current password verification."""
    from app.core.security import verify_password
    if not verify_password(payload.current_password, admin.password_hash):
        raise AppError(code="WRONG_PASSWORD", message="Current password is incorrect", status_code=400)
    if len(payload.new_password) < 8:
        raise AppError(code="WEAK_PASSWORD", message="New password must be at least 8 characters", status_code=400)
    new_hash = hash_password(payload.new_password)
    await db.execute(update(User).where(User.id == admin.id).values(password_hash=new_hash))
    await db.commit()
    return {"success": True, "message": "Password updated successfully"}


@router.get("/activity/recent")
async def get_recent_platform_activity(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    """Return recent cross-platform activity (work orders, violations, ARC, new users, new tenants)."""
    from sqlalchemy import union_all, literal
    from datetime import datetime

    # Fetch last N from each table and merge
    wo_res = await db.execute(
        select(
            WorkOrder.id,
            WorkOrder.created_at,
            WorkOrder.title.label("description"),
            Tenant.name.label("tenant_name"),
            literal("work_order").label("type"),
        )
        .join(Tenant, Tenant.id == WorkOrder.tenant_id)
        .order_by(WorkOrder.created_at.desc())
        .limit(limit)
    )
    vio_res = await db.execute(
        select(
            Violation.id,
            Violation.created_at,
            Violation.type.label("description"),
            Tenant.name.label("tenant_name"),
            literal("violation").label("type"),
        )
        .join(Tenant, Tenant.id == Violation.tenant_id)
        .order_by(Violation.created_at.desc())
        .limit(limit)
    )
    arc_res = await db.execute(
        select(
            ArcRequest.id,
            ArcRequest.created_at,
            ArcRequest.title.label("description"),
            Tenant.name.label("tenant_name"),
            literal("arc_request").label("type"),
        )
        .join(Tenant, Tenant.id == ArcRequest.tenant_id)
        .order_by(ArcRequest.created_at.desc())
        .limit(limit)
    )
    tu_res = await db.execute(
        select(
            TenantUser.id,
            TenantUser.created_at,
            User.name.label("description"),
            Tenant.name.label("tenant_name"),
            literal("new_user").label("type"),
        )
        .join(User, User.id == TenantUser.user_id)
        .join(Tenant, Tenant.id == TenantUser.tenant_id)
        .order_by(TenantUser.created_at.desc())
        .limit(limit)
    )
    tenant_res = await db.execute(
        select(
            Tenant.id,
            Tenant.created_at,
            Tenant.name.label("description"),
            Tenant.name.label("tenant_name"),
            literal("new_community").label("type"),
        )
        .order_by(Tenant.created_at.desc())
        .limit(limit)
    )

    events = []
    for row in [*wo_res.all(), *vio_res.all(), *arc_res.all(), *tu_res.all(), *tenant_res.all()]:
        events.append({
            "id": str(row.id),
            "type": row.type,
            "description": row.description,
            "tenant_name": row.tenant_name,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    # Sort all by created_at desc and take top N
    events.sort(key=lambda e: e["created_at"] or "", reverse=True)
    return events[:limit]


@router.get("/stats/detailed")
async def get_platform_detailed_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    from sqlalchemy import func
    from datetime import datetime, timedelta
    
    # 1. Provide a REAL timeline of platform activity over last 6 months
    now = datetime.utcnow()
    six_months_ago = now - timedelta(days=180)
    
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    # Query for monthly joins (new users)
    new_users_res = await db.execute(
        select(
            func.date_trunc("month", User.created_at).label("month"),
            func.count(User.id).label("cnt")
        )
        .where(User.created_at >= six_months_ago)
        .group_by(func.date_trunc("month", User.created_at))
        .order_by(func.date_trunc("month", User.created_at))
    )
    new_users_by_month = {row.month.month: row.cnt for row in new_users_res}

    # Query for monthly actions (work orders + violations + arc)
    wo_act_res = await db.execute(select(func.date_trunc("month", WorkOrder.created_at).label("m"), func.count(WorkOrder.id).label("c")).where(WorkOrder.created_at >= six_months_ago).group_by(func.date_trunc("month", WorkOrder.created_at)))
    vi_act_res = await db.execute(select(func.date_trunc("month", Violation.created_at).label("m"), func.count(Violation.id).label("c")).where(Violation.created_at >= six_months_ago).group_by(func.date_trunc("month", Violation.created_at)))
    ar_act_res = await db.execute(select(func.date_trunc("month", ArcRequest.created_at).label("m"), func.count(ArcRequest.id).label("c")).where(ArcRequest.created_at >= six_months_ago).group_by(func.date_trunc("month", ArcRequest.created_at)))
    
    activity_by_month = {}
    for r in wo_act_res: activity_by_month[r.m.month] = activity_by_month.get(r.m.month, 0) + r.c
    for r in vi_act_res: activity_by_month[r.m.month] = activity_by_month.get(r.m.month, 0) + r.c
    for r in ar_act_res: activity_by_month[r.m.month] = activity_by_month.get(r.m.month, 0) + r.c

    timeline = []
    current_month_idx = now.month - 1
    for i in range(7):
        target_date = now - timedelta(days=(6-i)*30)
        m_idx = target_date.month
        timeline.append({
            "name": months[m_idx - 1],
            "activities": activity_by_month.get(m_idx, 0),
            "newUsers": new_users_by_month.get(m_idx, 0),
        })

    # 2. Communities Table Data
    t_res = await db.execute(select(Tenant.id, Tenant.name, Tenant.slug, Tenant.status, Tenant.community_type))
    tenants = t_res.all()
    
    # Fast grouped aggregations
    u_res = await db.execute(select(TenantUser.tenant_id, func.count(TenantUser.id)).group_by(TenantUser.tenant_id))
    users_by_tenant = {row[0]: row[1] for row in u_res}
    
    w_res = await db.execute(select(WorkOrder.tenant_id, func.count(WorkOrder.id)).group_by(WorkOrder.tenant_id))
    workorders_by_tenant = {row[0]: row[1] for row in w_res}
    
    v_res = await db.execute(select(Violation.tenant_id, func.count(Violation.id)).group_by(Violation.tenant_id))
    violations_by_tenant = {row[0]: row[1] for row in v_res}
    
    a_res = await db.execute(select(ArcRequest.tenant_id, func.count(ArcRequest.id)).group_by(ArcRequest.tenant_id))
    arc_by_tenant = {row[0]: row[1] for row in a_res}
    
    d_res = await db.execute(select(Document.tenant_id, func.count(Document.id), func.sum(Document.size_bytes)).group_by(Document.tenant_id))
    docs_info_by_tenant = {row[0]: (row[1], row[2] or 0) for row in d_res}
    
    community_stats = []
    for t_id, t_name, t_slug, t_status, t_ctype in tenants:
        u_count = users_by_tenant.get(t_id, 0)
        w_count = workorders_by_tenant.get(t_id, 0)
        v_count = violations_by_tenant.get(t_id, 0)
        a_count = arc_by_tenant.get(t_id, 0)
        d_count, d_size = docs_info_by_tenant.get(t_id, (0, 0))
        
        # Real storage calculation
        storage_mb = d_size / (1024 * 1024)
        
        community_stats.append({
            "id": str(t_id),
            "name": t_name,
            "slug": t_slug,
            "status": t_status,
            "community_type": t_ctype,
            "users": u_count,
            "maintenance": w_count,
            "violations": v_count,
            "arc": a_count,
            "storage_mb": round(storage_mb, 2)
        })

    # Sort communities by highest users basically
    community_stats.sort(key=lambda x: x['users'], reverse=True)

    return {
        "timeline": timeline,
        "communities": community_stats
    }

@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    res = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    tenants = res.scalars().all()
    return [
        TenantOut(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            status=t.status,
            community_type=t.community_type,
            created_at=t.created_at
        ) for t in tenants
    ]

@router.post("/tenants", response_model=TenantOut)
async def create_tenant(
    payload: TenantCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    # 1. Create Tenant
    slug = payload.slug or str(uuid.uuid4())[:8]
    # Check slug uniqueness
    res = await db.execute(select(Tenant).where(Tenant.slug == slug))
    if res.scalar_one_or_none():
         raise AppError(code="SLUG_EXISTS", message="Community code already exists", status_code=400)

    tenant = Tenant(
        id=uuid.uuid4(),
        name=payload.name,
        slug=slug,
        community_type=payload.community_type or "APARTMENTS",
        status="ACTIVE"
    )
    db.add(tenant)
    await db.flush()

    # 2. Check/Create Admin User
    res = await db.execute(select(User).where(User.email == payload.admin_email))
    user = res.scalar_one_or_none()
    
    if not user:
        user = User(
            id=uuid.uuid4(),
            email=payload.admin_email,
            name=payload.admin_name,
            password_hash=hash_password(payload.admin_password),
            created_at=datetime.utcnow()
        )
        db.add(user)
        await db.flush()
    
    # 3. Create Main Building & Unit
    b = Building(id=uuid.uuid4(), tenant_id=tenant.id, name="Main Building")
    db.add(b)
    await db.flush()
    
    u = Unit(id=uuid.uuid4(), tenant_id=tenant.id, building_id=b.id, unit_number="0001")
    db.add(u)
    await db.flush()

    # 4. Link User
    tu = TenantUser(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        roles=["ADMIN"],
        unit_id=u.id,
        status="active"
    )
    db.add(tu)
    await db.commit()

    return TenantOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        community_type=tenant.community_type,
        created_at=tenant.created_at
    )

@router.get("/tenants/{tenant_id}", response_model=TenantDetailOut)
async def get_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    res = await db.execute(select(Tenant).where(Tenant.id == UUID(tenant_id)))
    tenant = res.scalar_one_or_none()
    if not tenant:
        raise AppError(code="NOT_FOUND", message="Tenant not found", status_code=404)
    
    return TenantDetailOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        community_type=tenant.community_type,
        created_at=tenant.created_at
    )

@router.put("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    q = update(Tenant).where(Tenant.id == UUID(tenant_id))
    values = {}
    if payload.name: values["name"] = payload.name
    if payload.slug: values["slug"] = payload.slug
    if payload.status: values["status"] = payload.status
    
    if values:
        await db.execute(q.values(**values))
        await db.commit()
    
    return {"ok": True}

@router.delete("/tenants/{tenant_id}")
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    tid = UUID(tenant_id)

    # Hard Delete: Delete everything related to this tenant to save space.
    # Order matters due to Foreign Keys.
    
    # 1. Deep ancillary (child rows first)
    await db.execute(delete(ArcReview).where(ArcReview.tenant_id == tid))
    await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.tenant_id == tid))
    await db.execute(delete(DocumentEmbedding).where(DocumentEmbedding.tenant_id == tid))
    await db.execute(delete(UserContact).where(UserContact.tenant_id == tid))
    await db.execute(delete(Hearing).where(Hearing.tenant_id == tid))
    await db.execute(delete(ViolationNotice).where(ViolationNotice.tenant_id == tid))

    # 2. Primary ancillary tables
    await db.execute(delete(Document).where(Document.tenant_id == tid))
    await db.execute(delete(DocumentFolder).where(DocumentFolder.tenant_id == tid))
    await db.execute(delete(WorkOrder).where(WorkOrder.tenant_id == tid))
    await db.execute(delete(Violation).where(Violation.tenant_id == tid))
    await db.execute(delete(ArcRequest).where(ArcRequest.tenant_id == tid))
    await db.execute(delete(Announcement).where(Announcement.tenant_id == tid))
    
    # 3. Financials
    await db.execute(delete(Payment).where(Payment.tenant_id == tid))
    await db.execute(delete(Charge).where(Charge.tenant_id == tid))
    await db.execute(delete(Invoice).where(Invoice.tenant_id == tid))
    await db.execute(delete(LedgerAccount).where(LedgerAccount.tenant_id == tid))
    
    # 4. People & profiles
    await db.execute(delete(ResidentProfile).where(ResidentProfile.tenant_id == tid))
    await db.execute(delete(Occupancy).where(Occupancy.tenant_id == tid))
    await db.execute(delete(TenantUser).where(TenantUser.tenant_id == tid))
    
    # 5. Property Structure
    # Units depend on Buildings, so Units first
    await db.execute(delete(Unit).where(Unit.tenant_id == tid))
    await db.execute(delete(Building).where(Building.tenant_id == tid))

    # 6. The Tenant itself
    await db.execute(delete(Tenant).where(Tenant.id == tid))
    
    await db.commit()
    return {"ok": True}

@router.get("/tenants/{tenant_id}/users", response_model=list[TenantUserOut])
async def get_tenant_users(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    res = await db.execute(
        select(User, TenantUser)
        .join(TenantUser, User.id == TenantUser.user_id)
        .where(TenantUser.tenant_id == UUID(tenant_id))
    )
    rows = res.all()
    
    return [
        TenantUserOut(
            id=str(u.id),
            name=u.name,
            email=u.email,
            role=tu.roles[0] if tu.roles else "USER",
            status=tu.status,
            created_at=tu.created_at
        )
        for u, tu in rows
    ]

class TenantUserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    status: str | None = None

@router.put("/tenants/{tenant_id}/users/{user_id}")
async def update_tenant_user(
    tenant_id: str,
    user_id: str,
    payload: TenantUserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    # Verify mapping
    res = await db.execute(select(TenantUser, User).join(User).where(
        TenantUser.tenant_id == UUID(tenant_id),
        TenantUser.user_id == UUID(user_id)
    ))
    row = res.one_or_none()
    if not row:
        raise AppError(code="NOT_FOUND", message="User not found in this tenant", status_code=404)
    tu, u = row

    if payload.name:
        u.name = payload.name
        db.add(u)
    
    if payload.email:
        # Check uniqueness
        res = await db.execute(select(User).where(User.email == payload.email, User.id != u.id))
        if res.scalar_one_or_none():
             raise AppError(code="EMAIL_EXISTS", message="Email exists", status_code=400)
        u.email = payload.email
        db.add(u)

    if payload.role:
        tu.roles = [payload.role]
        db.add(tu)
    
    if payload.status:
        tu.status = payload.status
        db.add(tu)

    await db.commit()
    return {"ok": True}

@router.delete("/tenants/{tenant_id}/users/{user_id}")
async def delete_tenant_user(
    tenant_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_platform_admin),
):
    tid = UUID(tenant_id)
    uid = UUID(user_id)

    res = await db.execute(select(TenantUser).where(
        TenantUser.tenant_id == tid,
        TenantUser.user_id == uid
    ))
    tu = res.scalar_one_or_none()
    if not tu:
        raise AppError(code="NOT_FOUND", message="User not found in tenant", status_code=404)
    
    # 1. ARC
    await db.execute(delete(ArcReview).where(ArcReview.tenant_id == tid, ArcReview.reviewer_user_id == uid))
    await db.execute(delete(ArcReview).where(ArcReview.tenant_id == tid, ArcReview.arc_request_id.in_(
        select(ArcRequest.id).where(ArcRequest.created_by_user_id == uid)
    )))
    await db.execute(delete(ArcRequest).where(ArcRequest.tenant_id == tid, ArcRequest.created_by_user_id == uid))

    # 2. Violations
    await db.execute(delete(Hearing).where(Hearing.tenant_id == tid, Hearing.violation_id.in_(
        select(Violation.id).where(Violation.created_by_user_id == uid)
    )))
    await db.execute(delete(ViolationNotice).where(ViolationNotice.tenant_id == tid, ViolationNotice.violation_id.in_(
        select(Violation.id).where(Violation.created_by_user_id == uid)
    )))
    await db.execute(delete(Violation).where(Violation.tenant_id == tid, Violation.created_by_user_id == uid))

    # 3. Work Orders
    try:
        await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.tenant_id == tid, WorkOrderEvent.actor_user_id == uid))
        await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.tenant_id == tid, WorkOrderEvent.work_order_id.in_(
            select(WorkOrder.id).where(WorkOrder.created_by_user_id == uid)
        )))
        await db.execute(update(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.assigned_to_user_id == uid).values(assigned_to_user_id=None))
        await db.execute(delete(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.created_by_user_id == uid))
    except Exception as e:
        import traceback
        traceback.print_exc()

    # 4. Documents
    await db.execute(delete(DocumentEmbedding).where(DocumentEmbedding.tenant_id == tid, DocumentEmbedding.document_id.in_(
        select(Document.id).where(Document.created_by_user_id == uid)
    )))
    await db.execute(update(Document).where(Document.tenant_id == tid, Document.folder_id.in_(
        select(DocumentFolder.id).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid)
    )).values(folder_id=None))
    await db.execute(delete(Document).where(Document.tenant_id == tid, Document.created_by_user_id == uid))
    # Note: Folder relationships complex, deleting user folders
    await db.execute(update(DocumentFolder).where(DocumentFolder.tenant_id == tid, DocumentFolder.parent_id.in_(
         select(DocumentFolder.id).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid)
    )).values(parent_id=None))
    await db.execute(delete(DocumentFolder).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid))

    # 5. Announcements
    await db.execute(delete(Announcement).where(Announcement.tenant_id == tid, Announcement.created_by_user_id == uid))

    # 6. Financials
    await db.execute(delete(Payment).where(Payment.tenant_id == tid, Payment.created_by_user_id == uid))
    await db.execute(delete(Charge).where(Charge.tenant_id == tid, Charge.created_by_user_id == uid))
    await db.execute(delete(Invoice).where(Invoice.tenant_id == tid, Invoice.created_by_user_id == uid))

    # 7. Profiles and Access
    await db.execute(delete(UserContact).where(UserContact.tenant_id == tid, UserContact.user_id == uid))
    await db.execute(delete(Occupancy).where(Occupancy.tenant_id == tid, Occupancy.user_id == uid))
    await db.execute(delete(ResidentProfile).where(ResidentProfile.tenant_id == tid, ResidentProfile.user_id == uid))
    
    await db.execute(delete(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.user_id == uid))
    await db.flush()

    # 8. Check for completely removing from system
    res = await db.execute(select(TenantUser).where(TenantUser.user_id == uid))
    remaining_links = res.scalars().all()
    if len(remaining_links) == 0:
        # Try to delete the User record completely. Wrap in nested transaction
        # to prevent rollback of the whole community deletion if there's a stray FK.
        try:
            async with db.begin_nested():
                await db.execute(delete(User).where(User.id == uid))
        except Exception:
            pass # Keep orphaned user record if system logs depend on it

    await db.commit()
    return {"ok": True}
