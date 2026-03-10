# backend/app/db/queries.py
from __future__ import annotations

from sqlalchemy import Select

def tenant_filter(stmt: Select, tenant_id: str):
    # Works for models that have tenant_id column.
    return stmt.where(getattr(stmt.column_descriptions[0]["entity"], "tenant_id") == tenant_id)
