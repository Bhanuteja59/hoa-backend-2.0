from pydantic import BaseModel
from uuid import UUID
from datetime import datetime

class UnitBase(BaseModel):
    unit_number: str
    building_id: UUID | None = None

class UnitCreate(UnitBase):
    pass

class UnitOut(UnitBase):
    id: UUID
    tenant_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True
