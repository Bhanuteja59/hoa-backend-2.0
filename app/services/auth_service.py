import logging
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import User, TenantUser, Tenant, Building
from app.core.security import verify_password, create_access_token, hash_password
from app.core.errors import AppError
from app.services.email_service import EmailService
from app.core.config import settings

class AuthService:
    
    @staticmethod
    async def login(payload, db: AsyncSession) -> dict:
        email = payload.email.lower().strip()
        
        # Find the user
        res = await db.execute(select(User).where(User.email == email))
        user = res.scalar_one_or_none()

        if not user:
            raise AppError(code="INVALID_LOGIN", message="Invalid email or password", status_code=401)

        # Check password
        if not verify_password(payload.password, user.password_hash):
            raise AppError(code="INVALID_LOGIN", message="Invalid email or password", status_code=401)

        # Get tenant link
        # Use scalars().all() instead of scalar_one_or_none() to support multiple HOAs
        res = await db.execute(select(TenantUser).where(TenantUser.user_id == user.id))
        tu_records = res.scalars().all()
        # Only allow login if the user has an active tenant account
        tu = next((t for t in tu_records if t.status == "active"), None)

        if not tu:
            if user.is_platform_admin:
                # Platform admins don't strictly need a tenant to log in
                access_token = create_access_token(
                    user_id=str(user.id),
                    tenant_id="00000000-0000-0000-0000-000000000000",
                    roles=["ADMIN"],
                    claims={
                        "is_platform_admin": True,
                        "pv": user.password_hash[:8]
                    }
                )
                return {
                    "access_token": access_token,
                    "token_type": "bearer",
                    "tenant_id": "00000000-0000-0000-0000-000000000000",
                    "user_id": str(user.id),
                    "tenant_slug": "platform",
                    "tenant_name": "Platform Administration",
                    "roles": ["ADMIN", "*"],
                    "name": user.name,
                    "is_platform_admin": True
                }
            raise AppError(code="NO_TENANT", message="User is not assigned to a tenant", status_code=403)

        # Fetch Tenant to get slug
        res = await db.execute(select(Tenant).where(Tenant.id == tu.tenant_id))
        tenant = res.scalar_one_or_none()
        tenant_slug = tenant.slug if tenant else None
        tenant_name = tenant.name if tenant else None

        # Create JWT
        access_token = create_access_token(
            user_id=str(user.id),
            tenant_id=str(tu.tenant_id),
            roles=tu.roles,
            claims={
                "is_platform_admin": user.is_platform_admin,
                "pv": user.password_hash[:8]
            }
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "tenant_id": str(tu.tenant_id),
            "user_id": str(user.id),
            "tenant_slug": tenant_slug,
            "tenant_name": tenant_name,
            "roles": tu.roles,
            "name": user.name,
            "is_platform_admin": user.is_platform_admin
        }

    @staticmethod
    async def get_current_user_profile(ctx, tenant, db: AsyncSession) -> dict:
        # Fetch unit_id for the user
        res = await db.execute(select(TenantUser).where(TenantUser.tenant_id == tenant.tenant_id, TenantUser.user_id == ctx.user_id))
        tu = res.scalar_one_or_none()
        unit_id = str(tu.unit_id) if tu and tu.unit_id else None

        # Fetch User to get phone
        res_u = await db.execute(select(User).where(User.id == ctx.user_id))
        user_row = res_u.scalar_one_or_none()
        
        return {
            "user_id": ctx.user_id, 
            "tenant_id": ctx.tenant_id, 
            "roles": ctx.roles, 
            "tenant_slug": tenant.slug,
            "tenant_name": tenant.name,
            "unit_id": unit_id,
            "status": getattr(tu, "status", "active"),
            "community_type": tenant.community_type,
            "phone": getattr(user_row, "phone", None),
            "address": getattr(tu, "address", None),
            "privacy_show_name": getattr(tu, "privacy_show_name", True),
            "privacy_show_email": getattr(tu, "privacy_show_email", False),
            "privacy_show_phone": getattr(tu, "privacy_show_phone", False),
            "privacy_show_address": getattr(tu, "privacy_show_address", False),
            "directory_visibility": getattr(tu, "directory_visibility", "RESIDENTS"),
        }

    @staticmethod
    async def register(payload, db: AsyncSession) -> dict:
        email = payload.email.lower()
        try:
            # 1️⃣ Check if user already exists
            user_res = await db.execute(select(User).where(User.email == email))
            user = user_res.scalar_one_or_none()
            
            tu = None
            if payload.token:
                # If a token is provided, prioritize finding the invitation by that token
                tu_res = await db.execute(select(TenantUser).where(TenantUser.invitation_token == payload.token))
                tu = tu_res.scalar_one_or_none()
                if tu:
                    # Token found, ensure the email matches to prevent token sharing/abuse
                    # Find the user associated with this invitation
                    tu_user_res = await db.execute(select(User).where(User.id == tu.user_id))
                    invited_user = tu_user_res.scalar_one_or_none()
                    if not invited_user or invited_user.email != email:
                        raise AppError(code="INVALID_TOKEN", message="This invitation link does not match the registration email.", status_code=400)
                    user = invited_user
            
            if not tu and user:
                # Fallback: Find the specific invitation if registration_number is provided
                tu_query = select(TenantUser).where(TenantUser.user_id == user.id)
                if payload.registration_number:
                    tu_query = tu_query.where(TenantUser.registration_number == payload.registration_number)
                
                # Fetch all TenantUser records for the user matching the criteria
                tu_res = await db.execute(tu_query)
                tu_records = tu_res.scalars().all()
                
                # Prioritize 'pending' status if multiple exist
                tu = next((t for t in tu_records if t.status == "pending"), tu_records[0] if tu_records else None)
            
            tenant = None # Initialize tenant for later use

            if user:
                can_merge = False
                if tu:
                    # For invited/pending users, we now MANDATE full triple-matching (Email, Reg Code, Account #)
                    if tu.status == "pending":
                        if not payload.registration_number or not payload.account_number:
                            raise AppError(code="MISSING_CREDENTIALS", message="Invited users must provide their Account and Registration numbers to sign up.", status_code=400)
                        
                        if tu.registration_number is not None and tu.registration_number != payload.registration_number:
                            raise AppError(code="INVALID_ACCOUNT", message="Registration number does not match record", status_code=400)
                        if tu.account_number is not None and tu.account_number != payload.account_number:
                            raise AppError(code="INVALID_ACCOUNT", message="Account number does not match record", status_code=400)
                        
                        # Check expiry
                        from datetime import datetime, timezone
                        if tu.invitation_expires_at and datetime.now(timezone.utc) > tu.invitation_expires_at:
                            raise AppError(code="EXPIRED_INVITE", message="Your registration number has expired (7 days limit). Please contact your administrator.", status_code=400)
                        
                        can_merge = True
                    else:
                        # Existing active user: standard merging logic if credentials provided correctly (optional for active users)
                        if payload.registration_number or payload.account_number:
                            if payload.registration_number and tu.registration_number is not None and tu.registration_number != payload.registration_number:
                                raise AppError(code="INVALID_ACCOUNT", message="Registration number does not match record", status_code=400)
                            if payload.account_number and tu.account_number is not None and tu.account_number != payload.account_number:
                                raise AppError(code="INVALID_ACCOUNT", message="Account number does not match record", status_code=400)
                            
                            if payload.registration_number and payload.account_number:
                                can_merge = True

                if can_merge:
                    logging.info(f"Merging registration for user: {email}")
                    # Update details
                    user.name = payload.full_name
                    user.password_hash = hash_password(payload.password)
                    if payload.phone:
                        user.phone = payload.phone
                    
                    tu.status = "active"
                    tu.invitation_token = None
                    
                    db.add(user)
                    db.add(tu)
                    
                    tenant_res = await db.execute(select(Tenant).where(Tenant.id == tu.tenant_id))
                    tenant = tenant_res.scalar_one()
                else:
                    logging.warning(f"Registration attempt for existing user: {email}")
                    raise AppError(
                        code="EMAIL_EXISTS",
                        message="Email is already registered. Please login or use a different email.",
                        status_code=400
                    )
            else:
                logging.info(f"Creating new user: {email}")
                # 2️⃣ Create a NEW user
                # Check if phone already exists
                if payload.phone:
                    res = await db.execute(select(User).where(User.phone == payload.phone))
                    if res.scalar_one_or_none():
                        logging.warning(f"Registration attempt with existing phone number: {payload.phone}")
                        raise AppError(
                            code="PHONE_EXISTS",
                            message="Phone number already exists",
                            status_code=400
                        )

                user = User(
                    email=email,
                    name=payload.full_name,
                    password_hash=hash_password(payload.password),
                    phone=payload.phone,
                    is_platform_admin=False
                )
                db.add(user)
                await db.flush() # Flush to get user.id for TenantUser link
            
            if not user or not user.id:
                 logging.error("User object or user ID is missing after creation/lookup.")
                 raise Exception("User creation/lookup failed")

            # 3️⃣ Create or Join Tenant (Only if not already merged from invitation)
            if not tu: # This means it's a brand new user, or an invited user without a TenantUser link (shouldn't happen if invited)
                assigned_role = "USER"
                if payload.role == "ADMIN" or payload.role == "BOARD_ADMIN":
                    assigned_role = "ADMIN"
                elif payload.role == "HOA_BOARD_MEMBER":
                    assigned_role = "BOARD_MEMBER"
                
                if assigned_role == "ADMIN":
                    if not payload.hoa_name:
                        raise AppError(code="MISSING_HOA_NAME", message="Community name is required to create a community", status_code=400)
                    
                    logging.info(f"Creating new tenant for admin: {payload.hoa_name}")
                    tenant = Tenant(
                        id=uuid.uuid4(),
                        name=payload.hoa_name,
                        slug=str(uuid.uuid4())[:8],
                        community_type=payload.community_type or "APARTMENTS",
                    )
                    db.add(tenant)
                    await db.flush() # Flush to get tenant.id
                else:
                    if not payload.tenant_slug:
                        raise AppError(code="MISSING_SLUG", message="Community code is required to join", status_code=400)
                        
                    # Joining existing tenant
                    slug_to_check = payload.tenant_slug.lower() if payload.tenant_slug else ""
                    logging.info(f"Attempting to join existing tenant with slug: {slug_to_check}")
                    res = await db.execute(select(Tenant).where(Tenant.slug == slug_to_check))
                    tenant = res.scalar_one_or_none()
                    if not tenant:
                        logging.warning(f"Invalid tenant slug provided: {slug_to_check}")
                        raise AppError(code="INVALID_SLUG", message="Community code invalid", status_code=400)
                    
                # Auto-activate everyone except maybe BOARD_MEMBER if they need approval
                # Based on the prompt: "while signuping the current status need to get active"
                # So we make user_status active for RESIDENT / USER too.
                user_status = "active" if assigned_role in ["ADMIN", "USER", "RESIDENT"] else "pending"

                # Check if building exists
                res = await db.execute(select(Building).where(Building.tenant_id == tenant.id).limit(1))
                b = res.scalar_one_or_none()
                if not b:
                    logging.info(f"Creating default building for tenant: {tenant.name}")
                    b = Building(id=uuid.uuid4(), tenant_id=tenant.id, name="Main Building")
                    db.add(b)
                    await db.flush() # Flush to get b.id

                admin_account_number = payload.account_number
                admin_registration_number = payload.registration_number

                if assigned_role == "ADMIN":
                    import random
                    if not admin_account_number:
                        admin_account_number = ''.join([str(random.randint(0, 9)) for _ in range(12)])
                    if not admin_registration_number:
                        admin_registration_number = ''.join([str(random.randint(0, 9)) for _ in range(6)])

                tu = TenantUser(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    roles=[assigned_role],
                    status=user_status,
                    registration_number=admin_registration_number,
                    account_number=admin_account_number
                )
                db.add(tu)
            
            # Determine the final assigned role and status for the access token and email
            assigned_role = tu.roles[0] if tu.roles else "USER"
            final_status = tu.status

            # 5️⃣ Save everything
            await db.commit()

        except AppError:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            logging.error(f"Registration failed: {str(e)}", exc_info=True)
            raise AppError(
                code="REGISTRATION_FAILED",
                message="An unexpected error occurred during registration. Please try again.",
                status_code=500
            ) from e

        access_token = create_access_token(
            user_id=str(user.id),
            tenant_id=str(tenant.id),
            roles=[assigned_role],
            claims={"pv": user.password_hash[:8]}
        )
        
        # 6️⃣ Send Welcome Email
        try:
            greeting_subject = "Welcome to HOA SaaS Platform"
            greeting_body = f"<h1>Welcome, {user.name}!</h1><p>Your account has been successfully created.</p>"
            frontend_url = settings.NEXTAUTH_URL.rstrip("/")
            
            if assigned_role == "ADMIN" or assigned_role == "BOARD_ADMIN":
                 greeting_subject = "Welcome to the Board - HOA Platform"
                 greeting_body = (
                     f"<h1>Welcome to the Board, {user.name}!</h1>"
                     f"<p>Your Board Admin account for <strong>{tenant.name}</strong> has been successfully created.</p>"
                     f"<h3>Your Account Details:</h3>"
                     f"<ul>"
                     f"<li><strong>Community Code:</strong> {tenant.slug}</li>"
                     f"<li><strong>Account Number:</strong> {tu.account_number}</li>"
                     f"<li><strong>Registration ID:</strong> {tu.registration_number}</li>"
                     f"<li><strong>Password:</strong> {payload.password}</li>"
                     f"</ul>"
                     f"<p>You can now start managing your community.</p>"
                     f"<p><a href='{frontend_url}'>Click here to access your dashboard</a></p>"
                 )
            elif assigned_role == "BOARD_MEMBER" or assigned_role == "HOA_BOARD_MEMBER":
                 greeting_subject = "Welcome to the Board - HOA Platform"
                 greeting_body = (
                     f"<h1>Welcome to the Board, {user.name}!</h1>"
                     f"<p>You have successfully registered to join <strong>{tenant.name}</strong> as a Board Member.</p>"
                     f"<h3>Your Account Details:</h3>"
                     f"<ul>"
                     f"<li><strong>Community Code:</strong> {tenant.slug}</li>"
                     f"<li><strong>Account Number:</strong> {tu.account_number}</li>"
                     f"<li><strong>Registration ID:</strong> {tu.registration_number}</li>"
                     f"<li><strong>Password:</strong> {payload.password}</li>"
                     f"</ul>"
                     f"<p>Your request is currently <strong>pending approval</strong> from the community administrator. We will notify you once your account is activated.</p>"
                     f"<p><a href='{frontend_url}'>Visit the platform here</a></p>"
                 )
            else:
                 greeting_subject = f"Welcome to {tenant.name}!"
                 greeting_body = (
                     f"<h1>Welcome, {user.name}!</h1>"
                     f"<p>You have successfully registered to join the <strong>{tenant.name}</strong> community as a Resident.</p>"
                     f"<h3>Your Account Details:</h3>"
                     f"<ul>"
                     f"<li><strong>Community Code:</strong> {tenant.slug}</li>"
                     f"<li><strong>Account Number:</strong> {tu.account_number}</li>"
                     f"<li><strong>Registration ID:</strong> {tu.registration_number}</li>"
                     f"<li><strong>Password:</strong> {payload.password}</li>"
                     f"</ul>"
                     f"<p>Your account is now <strong>active</strong> and you can start using the platform right away!</p>"
                     f"<p><a href='{frontend_url}'>Visit the platform here</a></p>"
                 )
            EmailService.send_email_background(
                to_email=user.email,
                subject=greeting_subject,
                html_body=greeting_body
            )
        except Exception as e:
            logging.warning(f"Failed to queue welcome email to {user.email}: {e}")

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user_id": str(user.id),
            "tenant_id": str(tenant.id)
        }

    @staticmethod
    async def regenerate_slug(ctx, tenant, db: AsyncSession) -> str:
        if "ADMIN" not in ctx.roles:
            raise AppError(code="NO_PERMISSION", message="Only admins can regenerate code", status_code=403)
            
        new_slug = str(uuid.uuid4())[:8]
        
        res = await db.execute(select(Tenant).where(Tenant.id == tenant.tenant_id))
        t_obj = res.scalar_one_or_none()
        if not t_obj:
            raise AppError(code="TENANT_NOT_FOUND", message="Tenant not found", status_code=404)
            
        t_obj.slug = new_slug
        await db.commit()
        
        return new_slug


