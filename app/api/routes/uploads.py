import os
import shutil
import uuid
from typing import Optional
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from app.core.config import settings

router = APIRouter(prefix="/uploads", tags=["Uploads"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Validate extension (basic)
        ext = file.filename.split(".")[-1].lower() if "." in file.filename else "tmp"
        if ext not in ["jpg", "jpeg", "png", "pdf", "docx", "txt"]:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        # Generate unique filename
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)

        # Save
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return URL (Relative to backend base)
        # Assuming backend mounts /uploads at root or /static
        # Let's assume we mount it at /static/uploads for safety or just /uploads
        # Returning full relative path.
        url = f"/uploads/{filename}"
        
        return {"url": url, "filename": filename, "original_name": file.filename}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
