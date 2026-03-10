# backend/app/services/embeddings.py
from __future__ import annotations

import hashlib
import math
from typing import Iterable

# MVP: deterministic fake embeddings so repo runs without external LLM.
# Replace with real embeddings provider in Phase 2 (OpenAI/Azure).
def fake_embed(text: str, dim: int = 384) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vals = []
    for i in range(dim):
        b = h[i % len(h)]
        vals.append(((b / 255.0) * 2.0) - 1.0)
    # normalize
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]
