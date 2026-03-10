# backend/app/services/storage.py
from __future__ import annotations

import os
from pathlib import Path
from app.core.config import settings

class Storage:
    def __init__(self) -> None:
        Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    async def put_stream(self, tenant_id: str, filename: str, file_obj) -> str:
        """Stream file content to storage."""
        safe = filename.replace("/", "_")
        key = f"{tenant_id}/{safe}"
        path = os.path.join(settings.UPLOAD_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, "wb") as f:
            while chunk := await file_obj.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)
        return key

    def put(self, tenant_id: str, filename: str, content: bytes) -> str:
        safe = filename.replace("/", "_")
        key = f"{tenant_id}/{safe}"
        path = os.path.join(settings.UPLOAD_DIR, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return key

    def get_path(self, key: str) -> str:
        return os.path.join(settings.UPLOAD_DIR, key)

    def get(self, key: str) -> bytes:
        """Retrieve document content from storage."""
        path = os.path.join(settings.UPLOAD_DIR, key)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Document not found: {key}")
        with open(path, "rb") as f:
            return f.read()
