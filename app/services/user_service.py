from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.db.models import (
    User, TenantUser, Unit, Building, Tenant, ArcReview, ArcRequest, Hearing,
    ViolationNotice, Violation, WorkOrderEvent, WorkOrder, DocumentEmbedding,
    DocumentFolder, Document, Announcement, Payment, Charge, Invoice,
    UserContact, Occupancy, ResidentProfile
)
from app.services.email_service import EmailService
from app.core.config import settings

class UserService:

    @staticmethod
    async def _resolve_unit(db: AsyncSession, tenant_id: UUID, unit_string: str) -> UUID:
        if "-" in unit_string:
            parts = unit_string.split("-", 1)
            building_name = parts[0].strip()
            unit_number = parts[1].strip()
        else:
            building_name = "Main Building"
            unit_number = unit_string.strip()

        # Find Unit directly (unique per tenant)
        res = await db.execute(select(Unit).where(
            Unit.tenant_id == tenant_id,
            Unit.unit_number == unit_number
        ))
        unit = res.scalar_one_or_none()
        
        if unit:
            return unit.id

        # Fallback: Find or Create Building if we need to create the unit
        res = await db.execute(select(Building).where(
            Building.tenant_id == tenant_id, 
            Building.name == building_name
        ))
        building = res.scalar_one_or_none()
        if not building:
            if building_name == "Main":
                 res = await db.execute(select(Building).where(Building.tenant_id == tenant_id, Building.name == "Main Building"))
                 building = res.scalar_one_or_none()

            if not building:
                building = Building(id=uuid4(), tenant_id=tenant_id, name=building_name, created_at=datetime.now(timezone.utc))
                db.add(building)
                await db.flush()

        # Create Unit
        res = await db.execute(select(Unit).where(
            Unit.tenant_id == tenant_id,
            Unit.building_id == building.id,
            Unit.unit_number == unit_number
        ))
        unit = res.scalar_one_or_none()
        
        if not unit:
            unit = Unit(
                id=uuid4(), 
                tenant_id=tenant_id, 
                building_id=building.id, 
                unit_number=unit_number, 
                created_at=datetime.now(timezone.utc)
            )
            db.add(unit)
            await db.flush()
        
        return unit.id

    @staticmethod
    async def list_users(ctx: AuthContext, tenant: TenantContext, db: AsyncSession, UserOut) -> list:
        res = await db.execute(
            select(User, TenantUser, Unit, Building, Tenant)
            .join(TenantUser, User.id == TenantUser.user_id)
            .outerjoin(Unit, TenantUser.unit_id == Unit.id)
            .outerjoin(Building, Unit.building_id == Building.id)
            .join(Tenant, TenantUser.tenant_id == Tenant.id)
            .where(TenantUser.tenant_id == tenant.tenant_id)
        )
        rows = res.all()
        
        is_admin = "ADMIN" in ctx.roles
        is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles

        output = []
        for u, tu, unit, b, t in rows:
            is_self = (u.id == ctx.user_id)
            
            visibility = getattr(tu, "directory_visibility", "RESIDENTS")
            
            if not is_admin and not is_self:
                if visibility == "HIDDEN":
                    continue
                if visibility == "BOARD" and not is_board:
                    continue
            
            show_name = getattr(tu, "privacy_show_name", True)
            show_email = getattr(tu, "privacy_show_email", False)
            show_phone = getattr(tu, "privacy_show_phone", False)
            show_address = getattr(tu, "privacy_show_address", False)

            name = u.name if (is_admin or is_board or is_self or show_name) else "Resident"
            email = u.email if (is_admin or is_board or is_self or show_email) else None
            phone = u.phone if (is_admin or is_board or is_self or show_phone) else None
            address = tu.address if (is_admin or is_board or is_self or show_address) else None

            output.append(
                UserOut(
                    id=str(u.id),
                    email=email or "hidden@example.com",
                    name=name,
                    role=tu.roles[0] if tu.roles else "USER",
                    status=getattr(tu, "status", "active"),
                    unit_id=str(tu.unit_id) if tu.unit_id else None,
                    unit_number=unit.unit_number if unit else None,
                    building_name=b.name if b else None,
                    address=address,
                    phone=phone,
                    community_type=t.community_type if t else None,
                    privacy_show_name=show_name,
                    privacy_show_email=show_email,
                    privacy_show_phone=show_phone,
                    privacy_show_address=show_address,
                    directory_visibility=visibility,
                    created_at=u.created_at,
                    registration_number=tu.registration_number,
                    account_number=tu.account_number
                )
            )
        return output

    @staticmethod
    async def create_user(payload, ctx: AuthContext, tenant: TenantContext, db: AsyncSession, UserOut) -> dict:
        email = payload.email.lower()

        res = await db.execute(select(User).where(User.email == email))
        user = res.scalar_one_or_none()
        
        if payload.phone:
            res_phone = await db.execute(select(User).where(User.phone == payload.phone))
            user_by_phone = res_phone.scalar_one_or_none()
            if user_by_phone and (not user or user.id != user_by_phone.id):
                raise AppError(code="PHONE_EXISTS", message="Phone number already exists", status_code=400)
        
        is_super_admin_request = (payload.role == "SUPER_ADMIN")
        if is_super_admin_request:
            if not ctx.user_id:
                 raise AppError(code="FORBIDDEN", message="Authentication required", status_code=401)
                 
            req_user_res = await db.execute(select(User).where(User.id == UUID(ctx.user_id)))
            req_user = req_user_res.scalar_one_or_none()
            if not req_user or not req_user.is_platform_admin:
                 raise AppError(code="FORBIDDEN", message="Only Super Admins can create other Super Admins", status_code=403)

        if payload.community_code:
            if not is_super_admin_request:
                 if not ctx.user_id:
                      raise AppError(code="FORBIDDEN", message="Authentication required", status_code=401)
                 req_user_res = await db.execute(select(User).where(User.id == UUID(ctx.user_id)))
                 req_user = req_user_res.scalar_one_or_none()
                 if not req_user or not req_user.is_platform_admin:
                      raise AppError(code="FORBIDDEN", message="Only Super Admins can specify community_code", status_code=403)
            
            t_res = await db.execute(select(Tenant).where(Tenant.slug == payload.community_code))
            target_tenant_obj = t_res.scalar_one_or_none()
            if not target_tenant_obj:
                 raise AppError(code="NOT_FOUND", message=f"Community with code '{payload.community_code}' not found", status_code=404)
            
            tenant = TenantContext(
                tenant_id=str(target_tenant_obj.id),
                slug=target_tenant_obj.slug,
                name=target_tenant_obj.name,
                community_type=target_tenant_obj.community_type
            )

        password_val = payload.password or str(uuid4()) # Temporary random if not provided (invited)
        
        if not user:
            user = User(
                id=uuid4(),
                email=email,
                name=payload.name,
                password_hash=hash_password(password_val) if payload.password else "", # Empty means needs to be set
                is_platform_admin=is_super_admin_request,
                created_at=datetime.now(timezone.utc)
            )
            db.add(user)
            await db.flush()
        else:
            if is_super_admin_request and not user.is_platform_admin:
                user.is_platform_admin = True
                db.add(user)
                await db.flush()
        
        res = await db.execute(select(TenantUser).where(
            TenantUser.tenant_id == tenant.tenant_id,
            TenantUser.user_id == user.id
        ))
        if res.scalar_one_or_none():
            raise AppError(code="EXISTS", message="Email address already exists in this tenant", status_code=400)

        unit_id = None
        if payload.unit:
            unit_id = await UserService._resolve_unit(db, tenant.tenant_id, payload.unit)

        # Invitation Logic: Trigger if no password provided for Residents or Board members
        from datetime import timedelta
        # All residents/board created by admin start as pending/invited for verification
        is_invite = (payload.role in ["USER", "BOARD", "BOARD_MEMBER"])
        invitation_token = None
        
        # Dual ID logic: Account Number (12 digits) & Registration Number (6 digits)
        registration_number = payload.registration_number # 6-digit invitation/claim code
        account_number = payload.account_number # 12-digit permanent ID

        if account_number:
            res_account = await db.execute(select(TenantUser).where(
                TenantUser.tenant_id == tenant.tenant_id,
                TenantUser.account_number == account_number
            ))
            if res_account.scalar_one_or_none():
                raise AppError(code="ACCOUNT_EXISTS", message="Account number already exists", status_code=400)
        
        status = "active"
        expires_at = None
        
        if is_invite:
            import secrets
            invitation_token = secrets.token_urlsafe(32)
            
            # Ensure permanent 12-digit ID exists
            if not account_number:
                account_number = "".join([str(secrets.randbelow(10)) for _ in range(12)])
            
            # Ensure temporary 6-digit registration/invite number exists
            if not registration_number:
                registration_number = "".join([str(secrets.randbelow(10)) for _ in range(6)])
            
            status = "pending"
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        tu = TenantUser(
            id=uuid4(),
            tenant_id=tenant.tenant_id,
            user_id=user.id,
            roles=[payload.role],
            unit_id=unit_id,
            address=payload.address,
            status=status,
            account_number=account_number,
            registration_number=registration_number,
            invitation_token=invitation_token,
            invite_sent_at=datetime.now(timezone.utc) if is_invite else None,
            invitation_expires_at=expires_at,
            created_at=datetime.now(timezone.utc)
        )
        db.add(tu)

        if payload.phone:
            user.phone = payload.phone
            db.add(user)

        await db.commit()

        try:
            frontend_url = settings.NEXTAUTH_URL.rstrip("/")
            if is_invite:
                subject = f"Welcome to {tenant.name} - Invitation to Join"
                invite_link = f"{frontend_url}/register?token={invitation_token}"
                html = (
                    f"<h1>Hello {user.name},</h1>"
                    f"<p>The administrator of <strong>{tenant.name}</strong> has invited you to join the community portal.</p>"
                    f"<div style='background: #f8fafc; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px; margin: 20px 0;'>"
                    f"<h3 style='margin-top: 0; color: #1e293b;'>Your Enrollment Details:</h3>"
                    f"<p style='margin: 5px 0;'><strong>HOA Name:</strong> {tenant.name}</p>"
                    f"<p style='margin: 5px 0;'><strong>Community Code:</strong> {tenant.slug}</p>"
                    f"<p style='margin: 5px 0;'><strong>Email:</strong> {user.email}</p>"
                    f"<p style='margin: 5px 0;'><strong>Account Number:</strong> <span style='font-family: monospace; font-weight: bold;'>{account_number}</span></p>"
                    f"<p style='margin: 5px 0;'><strong>Registration Code:</strong> <span style='font-family: monospace; font-weight: bold; color: #7c3aed;'>{registration_number}</span></p>"
                    f"</div>"
                    f"<p style='color: #ef4444; font-weight: bold;'>⚠️ This registration code is valid for 7 days only.</p>"
                    f"<p>Please click the link below to verify your identity and set your secure password:</p>"
                    f"<p><a href='{invite_link}' style='display: inline-block; padding: 12px 24px; background: #7c3aed; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;'>Join Community & Sign Up</a></p>"
                )
            else:
                subject = f"Your Access Added to {tenant.name}"
                html = (
                    f"<h1>Hello {user.name},</h1>"
                    f"<p>An account has been created for you in the <strong>{tenant.name}</strong> community.</p>"
                    f"<div style='background: #f8fafc; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px; margin: 20px 0;'>"
                    f"<p style='margin: 5px 0;'><strong>Community Code:</strong> {tenant.slug}</p>"
                    f"<p style='margin: 5px 0;'><strong>Email:</strong> {user.email}</p>"
                    f"<p style='margin: 5px 0;'><strong>Temporary Password:</strong> {payload.password}</p>"
                    f"<p style='margin: 5px 0;'><strong>Account Number:</strong> {account_number}</p>"
                    f"</div>"
                    f"<p>Please log in and change your password immediately.</p>"
                    f"<p><a href='{frontend_url}/login' style='display: inline-block; padding: 12px 24px; background: #0f172a; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;'>Login Now</a></p>"
                )
            
            EmailService.send_email_background(
                to_email=user.email,
                subject=subject,
                html_body=html
            )
        except Exception as e:
            import logging
            logging.warning(f"Failed to send account creation email to {user.email}: {e}")

        return UserOut(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=payload.role,
            status=status,
            unit_id=str(unit_id) if unit_id else None,
            unit_number=payload.unit if payload.unit else None,
            address=payload.address,
            phone=user.phone,
            community_type=tenant.community_type,
            registration_number=registration_number,
            account_number=account_number,
            created_at=user.created_at
        )

    @staticmethod
    async def update_user(user_id: str, payload, ctx: AuthContext, tenant: TenantContext, db: AsyncSession) -> dict:
        is_self = (user_id == ctx.user_id)
        is_admin = any(r in ctx.roles for r in ["ADMIN", "BOARD_ADMIN", "BOARD"])
        if not is_admin and not is_self:
            raise AppError(code="NO_PERMISSION", message="Only admins can edit other users", status_code=403)

        res = await db.execute(select(TenantUser).where(
            TenantUser.tenant_id == tenant.tenant_id,
            TenantUser.user_id == UUID(user_id)
        ))
        tu = res.scalar_one_or_none()
        if not tu:
            raise AppError(code="NOT_FOUND", message="User not found in this tenant", status_code=404)
            
        res_user = await db.execute(select(User).where(User.id == UUID(user_id)))
        target_user = res_user.scalar_one_or_none()

        old_status = getattr(tu, "status", "active")
            
        if payload.name:
            await db.execute(update(User).where(User.id == UUID(user_id)).values(name=payload.name))

        if payload.email:
            email = payload.email.lower()
            res = await db.execute(select(User).where(User.email == email, User.id != UUID(user_id)))
            if res.scalar_one_or_none():
                 raise AppError(code="EMAIL_EXISTS", message="Email already in use", status_code=400)
            
            await db.execute(update(User).where(User.id == UUID(user_id)).values(email=email))
            
        if payload.phone is not None:
            res_phone = await db.execute(select(User).where(User.phone == payload.phone, User.id != UUID(user_id)))
            if res_phone.scalar_one_or_none():
                 raise AppError(code="PHONE_EXISTS", message="Phone number already exists", status_code=400)
            await db.execute(update(User).where(User.id == UUID(user_id)).values(phone=payload.phone))
            
        if payload.role and is_admin:
             tu.roles = [payload.role]
             db.add(tu)

        if payload.status and is_admin:
             tu.status = payload.status
             db.add(tu)

        if payload.address is not None:
             tu.address = payload.address
             db.add(tu)

        if payload.unit is not None:
            can_edit_unit = is_admin or is_self
            if can_edit_unit:
                if payload.unit == "":
                     tu.unit_id = None
                else:
                     tu.unit_id = await UserService._resolve_unit(db, tenant.tenant_id, payload.unit)
                db.add(tu)

        if payload.privacy_show_name is not None:
            tu.privacy_show_name = payload.privacy_show_name
        if payload.privacy_show_email is not None:
            tu.privacy_show_email = payload.privacy_show_email
        if payload.privacy_show_phone is not None:
            tu.privacy_show_phone = payload.privacy_show_phone
        if payload.privacy_show_address is not None:
            tu.privacy_show_address = payload.privacy_show_address
        if payload.directory_visibility is not None:
            tu.directory_visibility = payload.directory_visibility
        if payload.community_type and is_admin:
            res_tenant = await db.execute(select(Tenant).where(Tenant.id == tenant.tenant_id))
            t = res_tenant.scalar_one_or_none()
            if t:
                 t.community_type = payload.community_type
                 db.add(t)

        if payload.registration_number is not None:
            tu.registration_number = payload.registration_number
        if payload.account_number is not None:
            if payload.account_number != tu.account_number:
                res_account = await db.execute(select(TenantUser).where(
                    TenantUser.tenant_id == tenant.tenant_id,
                    TenantUser.account_number == payload.account_number,
                    TenantUser.user_id != UUID(user_id)
                ))
                if res_account.scalar_one_or_none():
                    raise AppError(code="ACCOUNT_EXISTS", message="Account number already exists", status_code=400)
            tu.account_number = payload.account_number
        
        if any([
            payload.privacy_show_name is not None,
            payload.privacy_show_email is not None,
            payload.privacy_show_phone is not None,
            payload.privacy_show_address is not None,
            payload.directory_visibility is not None,
            payload.registration_number is not None,
            payload.account_number is not None
        ]):
            db.add(tu)

        await db.commit()

        if old_status == "pending" and payload.status == "active" and target_user and is_admin:
            try:
                frontend_url = "https://hoa-frontend-three.vercel.app/"
                details_html = "<ul>"
                if payload.role: details_html += f"<li><strong>Role Assigned:</strong> {payload.role}</li>"
                if payload.unit: details_html += f"<li><strong>Unit:</strong> {payload.unit}</li>"
                if payload.address: details_html += f"<li><strong>Address:</strong> {payload.address}</li>"
                details_html += "</ul>"

                body = (
                    f"<h1>Account Approved, {target_user.name}!</h1>"
                    f"<p>Great news! The Board Administrator for <strong>{tenant.name}</strong> has approved your account request.</p>"
                    f"<p>The administrator has confirmed your following details upon acceptance:</p>"
                    f"{details_html if details_html != '<ul></ul>' else '<p>(Standard Access)</p>'}"
                    f"<p>You can now log in and access the community portal.</p>"
                    f"<p><a href='{frontend_url}'>Click here to access your dashboard</a></p>"
                )
                EmailService.send_email(
                    to_email=target_user.email,
                    subject=f"Your Account has been Approved - {tenant.name}",
                    html_body=body
                )
            except Exception as e:
                import logging
                logging.warning(f"Failed to send approval email to {target_user.email}: {e}")

        return {"ok": True}

    @staticmethod
    async def delete_user(user_id: str, ctx: AuthContext, tenant: TenantContext, db: AsyncSession) -> dict:
        if user_id == ctx.user_id:
            raise AppError(code="SELF_DELETE", message="Cannot delete yourself", status_code=400)

        res = await db.execute(select(TenantUser).where(
            TenantUser.tenant_id == tenant.tenant_id,
            TenantUser.user_id == UUID(user_id)
        ))
        tu = res.scalar_one_or_none()
        if not tu:
            raise AppError(code="NOT_FOUND", message="User not found in this tenant", status_code=404)
            
        uid = UUID(user_id)
        tid = tenant.tenant_id
        
        # ARC
        await db.execute(delete(ArcReview).where(ArcReview.tenant_id == tid, ArcReview.reviewer_user_id == uid))
        await db.execute(delete(ArcReview).where(ArcReview.tenant_id == tid, ArcReview.arc_request_id.in_(
            select(ArcRequest.id).where(ArcRequest.created_by_user_id == uid)
        )))
        await db.execute(delete(ArcRequest).where(ArcRequest.tenant_id == tid, ArcRequest.created_by_user_id == uid))

        # Violations
        await db.execute(delete(Hearing).where(Hearing.tenant_id == tid, Hearing.violation_id.in_(
            select(Violation.id).where(Violation.created_by_user_id == uid)
        )))
        await db.execute(delete(ViolationNotice).where(ViolationNotice.tenant_id == tid, ViolationNotice.violation_id.in_(
            select(Violation.id).where(Violation.created_by_user_id == uid)
        )))
        await db.execute(delete(Violation).where(Violation.tenant_id == tid, Violation.created_by_user_id == uid))

        # Work Orders
        try:
            await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.tenant_id == tid, WorkOrderEvent.actor_user_id == uid))
            await db.execute(delete(WorkOrderEvent).where(WorkOrderEvent.tenant_id == tid, WorkOrderEvent.work_order_id.in_(
                select(WorkOrder.id).where(WorkOrder.created_by_user_id == uid)
            )))
            await db.execute(update(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.assigned_to_user_id == uid).values(assigned_to_user_id=None))
            await db.execute(delete(WorkOrder).where(WorkOrder.tenant_id == tid, WorkOrder.created_by_user_id == uid))
        except Exception as e:
            import logging
            logging.error(f"Failed to delete work orders for user {uid}: {e}")

        # Documents
        await db.execute(delete(DocumentEmbedding).where(DocumentEmbedding.tenant_id == tid, DocumentEmbedding.document_id.in_(
            select(Document.id).where(Document.created_by_user_id == uid)
        )))
        await db.execute(update(Document).where(Document.tenant_id == tid, Document.folder_id.in_(
            select(DocumentFolder.id).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid)
        )).values(folder_id=None))
        await db.execute(delete(Document).where(Document.tenant_id == tid, Document.created_by_user_id == uid))
        await db.execute(update(DocumentFolder).where(DocumentFolder.tenant_id == tid, DocumentFolder.parent_id.in_(
             select(DocumentFolder.id).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid)
        )).values(parent_id=None))
        await db.execute(delete(DocumentFolder).where(DocumentFolder.tenant_id == tid, DocumentFolder.created_by_user_id == uid))

        # Announcements & Financials & Profiles
        await db.execute(delete(Announcement).where(Announcement.tenant_id == tid, Announcement.created_by_user_id == uid))
        await db.execute(delete(Payment).where(Payment.tenant_id == tid, Payment.created_by_user_id == uid))
        await db.execute(delete(Charge).where(Charge.tenant_id == tid, Charge.created_by_user_id == uid))
        await db.execute(delete(Invoice).where(Invoice.tenant_id == tid, Invoice.created_by_user_id == uid))
        await db.execute(delete(UserContact).where(UserContact.tenant_id == tid, UserContact.user_id == uid))
        await db.execute(delete(Occupancy).where(Occupancy.tenant_id == tid, Occupancy.user_id == uid))
        await db.execute(delete(ResidentProfile).where(ResidentProfile.tenant_id == tid, ResidentProfile.user_id == uid))
        
        await db.execute(delete(TenantUser).where(TenantUser.tenant_id == tid, TenantUser.user_id == uid))
        await db.flush()

        res = await db.execute(select(TenantUser).where(TenantUser.user_id == uid))
        remaining_links = res.scalars().all()
        if len(remaining_links) == 0:
            try:
                async with db.begin_nested():
                    await db.execute(delete(User).where(User.id == uid))
            except Exception as e:
                import logging
                logging.error(f"Failed to cleanly delete user {uid} record: {e}")

        await db.commit()
        return {"ok": True}

    @staticmethod
    async def update_password(payload, ctx: AuthContext, db: AsyncSession) -> dict:
        res = await db.execute(select(User).where(User.id == UUID(ctx.user_id)))
        user = res.scalar_one_or_none()
        if not user:
            raise AppError(code="NOT_FOUND", message="User not found", status_code=404)

        if not user.password_hash:
             raise AppError(code="INVALID_AUTH", message="User has no password set (OAuth?)", status_code=400)

        if not verify_password(payload.current_password, user.password_hash):
            raise AppError(code="INVALID_PASSWORD", message="Incorrect current password", status_code=400)

        user.password_hash = hash_password(payload.new_password)
        db.add(user)

        await db.commit()
        return {"ok": True}

    @staticmethod
    async def list_my_contacts(ctx: AuthContext, tenant: TenantContext, db: AsyncSession, ContactOut) -> list:
        res = await db.execute(select(UserContact).where(
            UserContact.tenant_id == tenant.tenant_id,
            UserContact.user_id == UUID(ctx.user_id)
        ))
        return [
            ContactOut(
                id=str(c.id),
                user_id=str(c.user_id),
                name=c.name,
                relation=c.relation,
                email=c.email,
                phone=c.phone,
                is_primary=c.is_primary,
                address=c.address,
                created_at=c.created_at
            )
            for c in res.scalars().all()
        ]

    @staticmethod
    async def create_my_contact(payload, ctx: AuthContext, tenant: TenantContext, db: AsyncSession, ContactOut):
        if payload.is_primary:
            await db.execute(
                update(UserContact)
                .where(UserContact.tenant_id == tenant.tenant_id, UserContact.user_id == UUID(ctx.user_id))
                .values(is_primary=False)
            )

        contact = UserContact(
            id=uuid4(),
            tenant_id=tenant.tenant_id,
            user_id=UUID(ctx.user_id),
            name=payload.name,
            relation=payload.relation,
            email=payload.email,
            phone=payload.phone,
            is_primary=payload.is_primary,
            address=payload.address or {},
            created_at=datetime.now(timezone.utc)
        )
        db.add(contact)
        await db.commit()
        
        return ContactOut(
            id=str(contact.id),
            user_id=str(contact.user_id),
            name=contact.name,
            relation=contact.relation,
            email=contact.email,
            phone=contact.phone,
            is_primary=contact.is_primary,
            address=contact.address,
            created_at=contact.created_at
        )

    @staticmethod
    async def update_my_contact(contact_id: str, payload, ctx: AuthContext, tenant: TenantContext, db: AsyncSession, ContactOut):
        res = await db.execute(select(UserContact).where(
            UserContact.id == UUID(contact_id),
            UserContact.tenant_id == tenant.tenant_id,
            UserContact.user_id == UUID(ctx.user_id)
        ))
        contact = res.scalar_one_or_none()
        
        if not contact:
            raise AppError(code="NOT_FOUND", message="Contact not found", status_code=404)

        if payload.is_primary and not contact.is_primary:
            await db.execute(
                update(UserContact)
                .where(
                    UserContact.tenant_id == tenant.tenant_id, 
                    UserContact.user_id == UUID(ctx.user_id),
                    UserContact.id != contact.id
                )
                .values(is_primary=False)
            )

        contact.name = payload.name
        contact.relation = payload.relation
        contact.email = payload.email
        contact.phone = payload.phone
        contact.is_primary = payload.is_primary
        if payload.address is not None:
            contact.address = payload.address
            
        db.add(contact)

        await db.commit()
        
        return ContactOut(
            id=str(contact.id),
            user_id=str(contact.user_id),
            name=contact.name,
            relation=contact.relation,
            email=contact.email,
            phone=contact.phone,
            is_primary=contact.is_primary,
            address=contact.address,
            created_at=contact.created_at
        )

    @staticmethod
    async def delete_my_contact(contact_id: str, ctx: AuthContext, tenant: TenantContext, db: AsyncSession):
        res = await db.execute(select(UserContact).where(
            UserContact.id == UUID(contact_id),
            UserContact.tenant_id == tenant.tenant_id,
            UserContact.user_id == UUID(ctx.user_id)
        ))
        contact = res.scalar_one_or_none()
        
        if not contact:
            raise AppError(code="NOT_FOUND", message="Contact not found", status_code=404)
            
        await db.delete(contact)
        await db.commit()
        return {"ok": True}
