import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from app.core.config import settings
from app.services.cloudinary_service import cloudinary_service
from app.api.deps import get_tenant_ctx
from app.core.tenant import TenantContext

router = APIRouter(prefix="/uploads", tags=["Uploads"])

@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    tenant: TenantContext = Depends(get_tenant_ctx)
):
    """
    Uploads a file to Cloudinary and returns the secure URL.
    Used for attachments in Work Orders, ARC requests, and Violations.
    Organizes files by tenant slug.
    """
    try:
        # Validate filename
        if not file.filename:
             raise HTTPException(status_code=400, detail="Filename missing")
             
        filename_str = str(file.filename)
        ext = filename_str.split(".")[-1].lower() if "." in filename_str else "bin"
        
        # Allowed extensions
        allowed = ["jpg", "jpeg", "png", "gif", "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"]
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

        # Read file content
        content = await file.read()
        
        # Determine resource type for Cloudinary
        resource_type = "image" if ext in ["jpg", "jpeg", "png", "gif"] else "raw"
        
        # Upload to Cloudinary
        # Folder structure: hoa/{tenant_slug}/uploads
        folder = f"{tenant.slug}/uploads"
        
        result = await cloudinary_service.upload_file(
            file_content=content,
            filename=filename_str,
            folder=folder,
            resource_type=resource_type
        )
        
        if result["status"] == "error":
            raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {result['message']}")

        return {
            "url": result["url"], 
            "filename": result["public_id"], 
            "original_name": file.filename,
            "status": "success"
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
