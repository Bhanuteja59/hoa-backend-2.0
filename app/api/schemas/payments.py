from pydantic import BaseModel
from typing import Optional, Any

class PaymentCreate(BaseModel):
    amount: float
    currency: str = "USD"
    customer_id: Optional[str] = None
    description: Optional[str] = None
    email: Optional[str] = None

class PaymentResponse(BaseModel):
    client_secret: str
    status: str
    payment_id: str
    unit_id: Optional[str] = None
