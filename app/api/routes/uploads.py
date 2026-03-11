import os
import shutil
import uuid
from typing import Optional
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from app.core.config import settings

router = APIRouter(prefix="/uploads", tags=["Uploads"])

# Use absolute path for upload directory to avoid CWD issues
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except OSError:
    # Fallback for serverless environments with read-only filesystems (e.g., Vercel, AWS Lambda)
    UPLOAD_DIR = "/tmp/uploads"
    os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("")
async def upload_file(file: UploadFile = File(...)):
    """
    Saves a file to the local uploads directory and returns the relative URL.
    Used for attachments in Work Orders, ARC requests, and Violations.
    """
    try:
        # Validate filename
        if not file.filename:
             raise HTTPException(status_code=400, detail="Filename missing")
             
        filename_str = str(file.filename)
        ext = filename_str.split(".")[-1].lower() if "." in filename_str else "bin"
        
        # Allowed extensions (broadened to be safe)
        allowed = ["jpg", "jpeg", "png", "gif", "pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"]
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

        # Generate unique filename to avoid collisions and directory traversal
        secure_filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, secure_filename)

        # Ensure directory exists (redundant but safe)
        try:
            os.makedirs(UPLOAD_DIR, exist_ok=True)
        except OSError:
            pass

        # Save the file
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return the relative path which is served via StaticFiles mount
        url = f"/uploads/{secure_filename}"
        
        return {
            "url": url, 
            "filename": secure_filename, 
            "original_name": file.filename,
            "status": "success"
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
