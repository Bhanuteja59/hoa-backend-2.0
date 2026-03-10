from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_tenant_ctx, require, get_auth_ctx
from app.core.tenant import TenantContext
from app.core.rbac import AuthContext
from app.core.rag import rag_service
from pydantic import BaseModel

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

class ChatRequest(BaseModel):
    message: str
    history: list[dict] = [] # [{"role": "user", "content": "..."}]

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(require("chatbot:manage")), # Admin only
):
    if not file.filename.endswith(".pdf") and not file.filename.endswith(".txt"):
         raise HTTPException(status_code=400, detail="Only .pdf and .txt files are supported")

    content = await file.read()
    
    # Ingest to Qdrant with tenant isolation
    await rag_service.ingest_document(
        tenant_id=tenant.tenant_id,
        filename=file.filename,
        content=content,
        mime_type=file.content_type
    )
    
    return {"message": "Document ingested successfully"}

@router.post("/chat")
async def chat(
    payload: ChatRequest,
    tenant: TenantContext = Depends(get_tenant_ctx),
    ctx: AuthContext = Depends(get_auth_ctx), # Resident or Admin
    db: AsyncSession = Depends(get_db)
):
    if not payload.message.strip():
        return {"response": "Please ask a question."}

    # Query RAG with tenant isolation, db access, and user context
    answer = await rag_service.query(
        tenant_id=tenant.tenant_id,
        question=payload.message,
        history=payload.history,
        db=db,
        user_ctx=ctx
    )
    
    return {"response": answer}
