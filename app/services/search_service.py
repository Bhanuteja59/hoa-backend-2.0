from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.rbac import AuthContext
from app.db.models import User, TenantUser, WorkOrder, Announcement, Document, Violation

async def search_global_db(
    db: AsyncSession,
    tenant_id: str,
    user_ctx: AuthContext,
    q: str
) -> dict:
    if len(q) < 2:
        return {}

    filter_term = f"%{q}%"
    q_lower = q.lower()
    
    results = {
        "navigation": [],
        "residents": [],
        "work_orders": [],
        "announcements": [],
        "documents": [],
        "violations": []
    }

    # 0. Navigation Matches
    nav_map = [
        (["violation", "arc", "fine"], "/dashboard/violations-arc", "Violations & ARC"),
        (["work", "order", "maintain", "fix", "ticket"], "/dashboard/work-orders", "Work Orders"),
        (["doc", "file", "rule", "policy", "form"], "/dashboard/announcements-documents", "Documents"),
        (["announce", "news", "update", "post"], "/dashboard/announcements-documents", "Announcements"),
        (["setting", "profile", "account", "config"], "/dashboard/settings", "Settings"),
        (["ledger", "due", "pay", "balance"], "/dashboard/dues-ledger", "Dues & Ledger"),
    ]
    
    if "ADMIN" in user_ctx.roles:
        nav_map.append((["resident", "unit", "people", "tenant", "user"], "/dashboard/residents-units", "Residents & Units"))
        nav_map.append((["job", "queue", "task"], "/dashboard/jobs", "Jobs"))

    for keywords, url, title in nav_map:
        if any(k in q_lower for k in keywords):
            results["navigation"].append({"title": f"Go to {title}", "url": url, "type": "Page"})

    # 1. Residents (Admin only)
    if "ADMIN" in user_ctx.roles:
        stmt = (
            select(User.id, User.name, User.email)
            .join(TenantUser, User.id == TenantUser.user_id)
            .where(
                TenantUser.tenant_id == tenant_id,
                (User.name.ilike(filter_term) | User.email.ilike(filter_term))
            )
            .limit(5)
        )
        res = await db.execute(stmt)
        results["residents"] = [{"id": str(r.id), "name": r.name, "email": r.email} for r in res.all()]

    # 2. Work Orders
    stmt_wo = select(WorkOrder).where(
        WorkOrder.tenant_id == tenant_id, 
        (WorkOrder.title.ilike(filter_term) | WorkOrder.description.ilike(filter_term))
    )
    if "USER" in user_ctx.roles and "ADMIN" not in user_ctx.roles and "BOARD" not in user_ctx.roles:
        # Filter by own unit or creation for normal users
        # BOARD can see all
        # ADMIN can see all
         # Check if we need to fetch unit_id. For simplicity, filter by created_by if we don't have unit_id handy here.
         # But wait, search.py fetched unit_id.
         tu = (await db.execute(select(TenantUser).where(
            TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_ctx.user_id
         ))).scalar_one_or_none()
         
         if tu and tu.unit_id:
            stmt_wo = stmt_wo.where(WorkOrder.unit_id == tu.unit_id)
         else:
            stmt_wo = stmt_wo.where(WorkOrder.created_by_user_id == user_ctx.user_id)

    res_wo = await db.execute(stmt_wo.limit(5))
    results["work_orders"] = [{"id": str(w.id), "title": w.title, "status": w.status} for w in res_wo.scalars().all()]

    # 3. Announcements
    stmt_ann = select(Announcement).where(
        Announcement.tenant_id == tenant_id,
        (Announcement.title.ilike(filter_term) | Announcement.body.ilike(filter_term))
    )
    res_ann = await db.execute(stmt_ann.limit(5))
    results["announcements"] = [{"id": str(a.id), "title": a.title, "published_at": a.published_at} for a in res_ann.scalars().all()]

    # 4. Documents matches
    stmt_doc = select(Document).where(
        Document.tenant_id == tenant_id,
        (Document.title.ilike(filter_term) | Document.filename.ilike(filter_term))
    )
    if "ADMIN" not in user_ctx.roles and "BOARD" not in user_ctx.roles and "BOARD_MEMBER" not in user_ctx.roles:
        stmt_doc = stmt_doc.where(Document.acl.in_(["RESIDENT_VISIBLE", "public"]))

    res_doc = await db.execute(stmt_doc.limit(5))
    results["documents"] = [{"id": str(d.id), "title": d.title, "filename": d.filename} for d in res_doc.scalars().all()]

    # 5. Violations (Admin/Board only usually, but let's see search policy)
    # Search policy in rules:
    # "When the user is signup with the hoa-board-memeber they onlt see the works, violations, chat and setting s of rthem."
    # Board members can see all violations.
    # Normal users: usually see their own violations? 
    # Current search.py restricted to ADMIN.
    # Let's expand to BOARD and BOARD_MEMBER.
    if "ADMIN" in user_ctx.roles or "BOARD" in user_ctx.roles or "BOARD_MEMBER" in user_ctx.roles:
        stmt_vio = select(Violation).where(
            Violation.tenant_id == tenant_id,
            (Violation.description.ilike(filter_term) | Violation.type.ilike(filter_term))
        )
        res_vio = await db.execute(stmt_vio.limit(5))
        results["violations"] = [{"id": str(v.id), "description": v.description, "status": v.status} for v in res_vio.scalars().all()]

    return results
