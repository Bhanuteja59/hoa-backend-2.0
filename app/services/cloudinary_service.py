import cloudinary
import cloudinary.uploader
import cloudinary.api
from app.core.config import settings
import uuid

# Configuration
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

    @staticmethod
    async def delete_file_by_url(url: str, resource_type: str = "auto") -> bool:
        """
        Deletes a file from Cloudinary by analyzing its URL to extract the public_id.
        """
        try:
            # url: https://res.cloudinary.com/<cloud>/raw/upload/v123/hoa/slug/documents/uuid.pdf
            # find "upload/"
            if "upload/" not in url:
                return False
            
            parts = url.split("upload/")
            after_version = parts[1].split("/", 1)[1] # skip the v12345 part
            public_id = after_version # For raw files or images without extension strip requirement. Actually 'destroy' needs exact public_id.
            
            # For images, public_id doesn't include extension. For raw files like PDF, it DOES typically include extension in public_id, or maybe not? 
            # Actually, using cloudinary API, sometimes it does. A safer way is trying both.
            # But let's just strip the extension if it's an image. Let's strip it to be safe.
            from urllib.parse import unquote
            public_id = unquote(public_id)
            public_id_no_ext = public_id.rsplit(".", 1)[0]
            
            await run_in_threadpool(
                cloudinary.uploader.destroy,
                public_id_no_ext,
                resource_type="image" # image resource type covers images and pdfs usually, or "raw"
            )
            
            await run_in_threadpool(
                cloudinary.uploader.destroy,
                public_id, # full with extension (for 'raw' resource types)
                resource_type="raw"
            )
            return True
        except Exception as e:
            print(f"Cloudinary delete failed: {str(e)}")
            return False

cloudinary_service = CloudinaryService()
