import cloudinary
import cloudinary.uploader
import cloudinary.api
from app.core.config import settings
import uuid

# Configuration
if settings.CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL, secure=True)
else:
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True
    )

from fastapi.concurrency import run_in_threadpool

class CloudinaryService:
    @staticmethod
    async def upload_file(
        file_content: bytes, 
        filename: str, 
        folder: str = "general",
        resource_type: str = "auto"
    ) -> dict:
        """
        Uploads a file to Cloudinary.
        Folder structure: hoa/{tenant_slug}/uploads/{folder}/
        """
        try:
            # Generate a unique public ID
            unique_id = f"{uuid.uuid4().hex}"
            
            # Run blocking Cloudinary upload in a threadpool
            upload_result = await run_in_threadpool(
                cloudinary.uploader.upload,
                file_content,
                public_id=unique_id,
                folder=f"hoa/{folder}",
                resource_type=resource_type,
                overwrite=True
            )
            
            return {
                "url": upload_result.get("secure_url"),
                "public_id": upload_result.get("public_id"),
                "format": upload_result.get("format"),
                "resource_type": upload_result.get("resource_type"),
                "status": "success"
            }
        except Exception as e:
            print(f"Cloudinary upload failed: {str(e)}")
            return {
                "status": "error",
                "message": str(e)
            }

cloudinary_service = CloudinaryService()
