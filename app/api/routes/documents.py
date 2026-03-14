# backend/app/api/routes/documents.py
from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Response
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant_ctx, require, get_auth_ctx
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.errors import AppError
from app.db.models import Document, DocumentFolder, TenantUser, Notification
from app.services.storage import Storage
from app.services.cloudinary_service import cloudinary_service
from app.services.notifications import notification_manager
from app.core.rag import rag_service
from sqlalchemy.orm import undefer

router = APIRouter(prefix="/documents", tags=["documents"])


# ─────────────────────────────────────────────
#  Pydantic schemas
# ─────────────────────────────────────────────

class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime


class FolderCreateIn(BaseModel):
    name: str
    parent_id: str | None = None


class FolderRenameIn(BaseModel):
    name: str


class DocumentOut(BaseModel):
    id: str
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    acl: str
    folder_id: str | None
    created_at: datetime


# ─────────────────────────────────────────────
#  Folder endpoints
# ─────────────────────────────────────────────

@router.get("/folders", response_model=list[FolderOut])
async def list_folders(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:read")),
):
    """List all folders for the tenant (all roles)."""
    res = await db.execute(
        select(DocumentFolder)
        .where(DocumentFolder.tenant_id == UUID(tenant.tenant_id))
        .order_by(DocumentFolder.name)
    )
    folders = res.scalars().all()
    return [
        FolderOut(id=str(f.id), name=f.name, parent_id=str(f.parent_id) if f.parent_id else None, created_at=f.created_at)
        for f in folders
    ]


@router.post("/folders", response_model=FolderOut)
async def create_folder(
    payload: FolderCreateIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:write")),
):
    """Create a folder. ADMIN only."""
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can create folders", status_code=403)

    parent_uuid = UUID(payload.parent_id) if payload.parent_id else None

    # Validate parent belongs to same tenant
    if parent_uuid:
        res = await db.execute(select(DocumentFolder).where(
            DocumentFolder.id == parent_uuid,
            DocumentFolder.tenant_id == UUID(tenant.tenant_id)
        ))
        if not res.scalar_one_or_none():
            raise AppError(code="NOT_FOUND", message="Parent folder not found", status_code=404)

    folder = DocumentFolder(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        parent_id=parent_uuid,
        name=payload.name,
        created_by_user_id=UUID(ctx.user_id),
        created_at=datetime.now(timezone.utc),
    )
    db.add(folder)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise AppError(code="CONFLICT", message="A folder with this name already exists here", status_code=409)

    return FolderOut(id=str(folder.id), name=folder.name, parent_id=str(folder.parent_id) if folder.parent_id else None, created_at=folder.created_at)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: str,
    payload: FolderRenameIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:write")),
):
    """Rename a folder. ADMIN only."""
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can rename folders", status_code=403)

    res = await db.execute(select(DocumentFolder).where(
        DocumentFolder.id == UUID(folder_id),
        DocumentFolder.tenant_id == UUID(tenant.tenant_id)
    ))
    folder = res.scalar_one_or_none()
    if not folder:
        raise AppError(code="NOT_FOUND", message="Folder not found", status_code=404)

    folder.name = payload.name
    db.add(folder)
    await db.commit()
    return FolderOut(id=str(folder.id), name=folder.name, parent_id=str(folder.parent_id) if folder.parent_id else None, created_at=folder.created_at)


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:write")),
):
    """Delete a folder and all its documents. ADMIN only."""
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can delete folders", status_code=403)

    folder_uuid = UUID(folder_id)
    res = await db.execute(select(DocumentFolder).where(
        DocumentFolder.id == folder_uuid,
        DocumentFolder.tenant_id == UUID(tenant.tenant_id)
    ))
    folder = res.scalar_one_or_none()
    if not folder:
        raise AppError(code="NOT_FOUND", message="Folder not found", status_code=404)

    # Recursively collect all subfolder IDs to delete
    all_folder_ids = await _collect_folder_ids(db, UUID(tenant.tenant_id), folder_uuid)

    # Delete all documents in these folders (and clean up cloud/AI)
    docs_to_delete_res = await db.execute(select(Document).where(
        Document.tenant_id == UUID(tenant.tenant_id),
        Document.folder_id.in_(all_folder_ids)
    ))
    docs_to_delete = docs_to_delete_res.scalars().all()
    
    for doc in docs_to_delete:
        # Delete from Cloudinary
        if doc.storage_key and doc.storage_key.startswith("http"):
            try:
                await cloudinary_service.delete_file_by_url(doc.storage_key)
            except Exception:
                pass
        # Delete from Qdrant
        try:
            if doc.filename.lower().endswith((".pdf", ".txt")):
                await rag_service.delete_document(tenant.tenant_id, doc.filename)
        except Exception:
            pass
            
        await db.delete(doc)

    # Delete subfolders (deepest first — delete all at once since SQLAlchemy handles FK order)
    for fid in all_folder_ids:
        if fid != folder_uuid:
            await db.execute(delete(DocumentFolder).where(DocumentFolder.id == fid))

    await db.delete(folder)
    await db.commit()
    return {"ok": True}


async def _collect_folder_ids(db: AsyncSession, tenant_id: UUID, root_id: UUID) -> list[UUID]:
    """Recursively collect folder IDs including root."""
    result = [root_id]
    res = await db.execute(select(DocumentFolder).where(
        DocumentFolder.tenant_id == tenant_id,
        DocumentFolder.parent_id == root_id
    ))
    children = res.scalars().all()
    for child in children:
        result.extend(await _collect_folder_ids(db, tenant_id, child.id))
    return result


# ─────────────────────────────────────────────
#  Document endpoints
# ─────────────────────────────────────────────

@router.get("", response_model=list[DocumentOut])
async def list_documents(
    folder_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:read")),
):
    """List documents. Optional ?folder_id= filter. Pass 'root' for root-level docs."""
    stmt = select(Document).where(Document.tenant_id == UUID(tenant.tenant_id))

    if folder_id == "root" or folder_id == "":
        stmt = stmt.where(Document.folder_id == None)   # noqa: E711
    elif folder_id:
        stmt = stmt.where(Document.folder_id == UUID(folder_id))

    # Non-admins only see public docs
    is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
    if not is_admin_or_board:
        stmt = stmt.where(Document.acl.in_(["RESIDENT_VISIBLE", "public"]))
    res = await db.execute(stmt.order_by(Document.created_at.desc()))
    docs = res.scalars().all()

    return [
        DocumentOut(
            id=str(d.id),
            title=d.title,
            filename=d.filename,
            mime_type=d.mime_type,
            size_bytes=d.size_bytes,
            acl=d.acl,
            folder_id=str(d.folder_id) if d.folder_id else None,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.post("", response_model=DocumentOut)
async def upload_document(
    title: str = Form(...),
    acl: str = Form("RESIDENT_VISIBLE"),
    folder_id: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:write")),
):
    """Upload a document. ADMIN only."""
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can upload documents", status_code=403)

    # Validate folder belongs to tenant
    folder_uuid = None
    if folder_id:
        folder_uuid = UUID(folder_id)
        res = await db.execute(select(DocumentFolder).where(
            DocumentFolder.id == folder_uuid,
            DocumentFolder.tenant_id == UUID(tenant.tenant_id)
        ))
        if not res.scalar_one_or_none():
            raise AppError(code="NOT_FOUND", message="Folder not found", status_code=404)

    content = await file.read()
    file_size = len(content)

    mime_type = file.content_type
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename)
        if guessed:
            mime_type = guessed

    # 1. Upload to Cloudinary
    storage_key = f"db:{file.filename}"
    doc_content = content
    try:
        folder = f"{tenant.slug}/documents"
        c_res = await cloudinary_service.upload_file(
            file_content=content,
            filename=file.filename,
            folder=folder,
            resource_type="auto"
        )
        if c_res.get("status") == "success":
            storage_key = c_res.get("url")
            doc_content = None # Not saving blob to DB if we successfully used Cloudinary
    except Exception as e:
        print(f"Cloudinary fallback error: {e}")

    # 2. Add to Chatbot (rag_service)
    try:
        if file.filename.lower().endswith(".pdf") or file.filename.lower().endswith(".txt"):
            await rag_service.ingest_document(
                tenant_id=tenant.tenant_id,
                filename=file.filename,
                content=content,
                mime_type=mime_type or "application/octet-stream"
            )
    except Exception as e:
        print(f"RAG ingest error: {e}")

    doc = Document(
        id=uuid4(),
        tenant_id=UUID(tenant.tenant_id),
        title=title,
        filename=file.filename,
        mime_type=mime_type or "application/octet-stream",
        size_bytes=file_size,
        acl=acl,
        storage_key=storage_key,
        content=doc_content,
        folder_id=folder_uuid,
        created_by_user_id=UUID(ctx.user_id),
        created_at=datetime.now(timezone.utc),
    )
    db.add(doc)
    
    # Notify residents if document is visible to them
    if acl == "RESIDENT_VISIBLE":
        # Find all active residents/board in this tenant (excluding platform admins)
        stmt = select(TenantUser.user_id).where(
            TenantUser.tenant_id == UUID(tenant.tenant_id),
            TenantUser.status == "active"
        )
        res = await db.execute(stmt)
        user_ids = res.scalars().all()
        for uid in user_ids:
            if uid == UUID(ctx.user_id): continue # Skip uploader
            n = Notification(
                tenant_id=UUID(tenant.tenant_id),
                user_id=uid,
                title="New Document Available",
                message=f"A new document '{title}' has been shared with the community.",
                type="document",
                link="/dashboard/documents"
            )
            db.add(n)
            await notification_manager.notify_user(uid, n.title, n.message, n.type, n.link)

    await db.commit()

    return DocumentOut(
        id=str(doc.id),
        title=doc.title,
        filename=doc.filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        acl=doc.acl,
        folder_id=str(doc.folder_id) if doc.folder_id else None,
        created_at=doc.created_at,
    )


@router.get("/my-stats")
async def get_my_document_stats(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    from sqlalchemy import func
    res = await db.execute(
        select(
            func.count(Document.id).label("total_count"),
            func.sum(Document.size_bytes).label("total_size_bytes")
        ).where(
            Document.tenant_id == UUID(tenant.tenant_id),
            Document.created_by_user_id == UUID(ctx.user_id)
        )
    )
    row = res.one_or_none()
    if row:
        return {"total_count": row.total_count or 0, "total_size_bytes": row.total_size_bytes or 0}
    return {"total_count": 0, "total_size_bytes": 0}

@router.get("/my-documents", response_model=list[DocumentOut])
async def list_my_documents(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx),
):
    res = await db.execute(
        select(Document)
        .where(
            Document.tenant_id == UUID(tenant.tenant_id),
            Document.created_by_user_id == UUID(ctx.user_id)
        )
        .order_by(Document.created_at.desc())
    )
    docs = res.scalars().all()
    return [
        DocumentOut(
            id=str(d.id),
            title=d.title,
            filename=d.filename,
            mime_type=d.mime_type,
            size_bytes=d.size_bytes,
            acl=d.acl,
            folder_id=str(d.folder_id) if d.folder_id else None,
            created_at=d.created_at,
        )
        for d in docs
    ]
@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:write")),
):
    """Delete a document. ADMIN only."""
    is_admin = "ADMIN" in ctx.roles
    is_board = "BOARD" in ctx.roles or "BOARD_MEMBER" in ctx.roles
    if not (is_admin or is_board):
        raise AppError(code="NO_PERMISSION", message="Only admins/board can delete documents", status_code=403)

    res = await db.execute(select(Document).where(
        Document.tenant_id == UUID(tenant.tenant_id),
        Document.id == UUID(document_id)
    ))
    d = res.scalar_one_or_none()
    if not d:
        raise AppError(code="NOT_FOUND", message="Document not found", status_code=404)

    # 1. Delete from Cloudinary if it's stored there
    if d.storage_key and d.storage_key.startswith("http"):
        try:
            await cloudinary_service.delete_file_by_url(d.storage_key)
        except Exception as e:
            print(f"Failed to delete {d.storage_key} from Cloudinary: {e}")
            
    # 2. Delete from Qdrant Chatbot
    try:
        if d.filename.lower().endswith((".pdf", ".txt")):
            await rag_service.delete_document(tenant.tenant_id, d.filename)
    except Exception as e:
        print(f"Failed to delete {d.filename} from Qdrant: {e}")

    await db.delete(d)
    await db.commit()
    return {"ok": True}


class DocTokenOut(BaseModel):
    token: str
    url: str


@router.get("/{document_id}/token", response_model=DocTokenOut)
async def get_document_token(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("docs:read")),
):
    """Generate a temporary access token for viewing the document."""
    from app.core.config import settings
    from jose import jwt, JWTError
    from datetime import timedelta

    res = await db.execute(select(Document).where(
        Document.tenant_id == UUID(tenant.tenant_id),
        Document.id == UUID(document_id)
    ))
    d = res.scalar_one_or_none()
    if not d:
        raise AppError(code="NOT_FOUND", message="Document not found", status_code=404)

    if d.acl in ["BOARD_ONLY", "private"]:
        is_admin_or_board = any(r in ctx.roles for r in ["ADMIN", "BOARD", "BOARD_MEMBER"])
        if not is_admin_or_board:
            raise AppError(code="NO_PERMISSION", message="You don't have access to this document", status_code=403)
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    to_encode = {
        "sub": "doc_view",
        "doc_id": str(d.id),
        "tenant_id": tenant.tenant_id,
        "exp": expire
    }
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    url = f"/documents/view/{d.id}?token={encoded_jwt}"
    return DocTokenOut(token=encoded_jwt, url=url)


@router.get("/view/{document_id}")
async def view_document_with_token(
    document_id: str,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """View a document using a temporary token (bypass standard auth)."""
    from app.core.config import settings
    from jose import jwt, JWTError
    from fastapi.responses import Response
    import os

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        token_doc_id = payload.get("doc_id")
        token_tenant_id = payload.get("tenant_id")
        if token_doc_id != document_id:
            raise AppError(code="INVALID_TOKEN", message="Invalid token for this document", status_code=403)
    except JWTError:
        raise AppError(code="INVALID_TOKEN", message="Invalid or expired token", status_code=403)

    stmt = select(Document).options(undefer(Document.content)).where(Document.id == UUID(document_id))
    res = await db.execute(stmt)
    d = res.scalar_one_or_none()

    if not d:
        raise AppError(code="NOT_FOUND", message="Document not found", status_code=404)
    if str(d.tenant_id) != token_tenant_id:
        raise AppError(code="NO_PERMISSION", message="Tenant mismatch", status_code=403)

    # Serve directly from DB content if stored as blob
    if d.content:
        return Response(
            content=d.content,
            media_type=d.mime_type,
            headers={"Content-Disposition": f'inline; filename="{d.filename}"'},
        )

    if d.storage_key:
        if d.storage_key.startswith("http"):
            import time
            import httpx
            import cloudinary.utils
            from fastapi.responses import StreamingResponse

            storage_url = d.storage_key
            fetch_url = storage_url  # may be replaced with signed URL

            if "cloudinary.com" in storage_url:
                try:
                    r_type = "raw" if "/raw/" in storage_url else "image"

                    # Extract public_id from the Cloudinary URL
                    parts = storage_url.split("upload/")
                    if len(parts) > 1:
                        after_upload = parts[1].split("/", 1)
                        # Skip version segment like "v1741948332"
                        if (after_upload[0].startswith("v")
                                and after_upload[0][1:].isdigit()
                                and len(after_upload) > 1):
                            p_id = after_upload[1]
                        else:
                            p_id = "/".join(after_upload)

                        # Image public_ids don't include extension; raw files do
                        if r_type == "image" and "." in p_id.split("/")[-1]:
                            p_id = p_id.rsplit(".", 1)[0]

                        # Generate signed URL valid for 1 hour
                        signed_url, _ = cloudinary.utils.cloudinary_url(
                            p_id,
                            resource_type=r_type,
                            type="upload",
                            secure=True,
                            sign_url=True,
                            expires_at=int(time.time()) + 3600,
                        )
                        fetch_url = signed_url
                except Exception as sign_err:
                    print(f"Cloudinary URL signing failed: {sign_err}, trying unsigned URL")

            # Proxy the file back to the browser so we control the Content-Type.
            # This is essential — Cloudinary raw files are served as application/octet-stream,
            # which browsers won't render inline. We force the correct MIME type from the DB.
            try:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    async with client.stream("GET", fetch_url) as resp:
                        if resp.status_code != 200:
                            raise AppError(
                                code="FILE_NOT_FOUND",
                                message=f"Cloud storage returned {resp.status_code}",
                                status_code=502,
                            )

                        # Use the mime_type from DB (reliable) not from Cloudinary headers
                        content_type = d.mime_type or resp.headers.get("content-type", "application/octet-stream")

                        headers = {
                            "Content-Disposition": f'inline; filename="{d.filename}"',
                            "Cache-Control": "private, max-age=3600",
                        }
                        cl = resp.headers.get("content-length")
                        if cl:
                            headers["Content-Length"] = cl

                        # We must read all content before the context manager closes
                        content = await resp.aread()

                return Response(
                    content=content,
                    media_type=content_type,
                    headers=headers,
                )
            except AppError:
                raise
            except Exception as e:
                raise AppError(code="PROXY_ERROR", message=f"Failed to fetch document: {e}", status_code=502)

        from fastapi.responses import FileResponse
        storage = Storage()
        path = storage.get_path(d.storage_key)
        if path and os.path.exists(path):
            return FileResponse(path, media_type=d.mime_type, filename=d.filename, content_disposition_type="inline")

    raise AppError(code="FILE_NOT_FOUND", message="Document content not found", status_code=404)

