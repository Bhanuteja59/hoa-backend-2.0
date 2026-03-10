# backend/app/services/documents.py
from __future__ import annotations

import hashlib


from app.services.embeddings import fake_embed

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    chunks = []
    i = 0
   
