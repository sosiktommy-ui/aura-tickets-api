from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class TicketCreate(BaseModel):
    order_id: str
    transaction_id: Optional[str] = None
    customer_name: str
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    ticket_type: Optional[str] = "Standard"
    event_date: Optional[str] = None
    event_name: Optional[str] = None
    price: Optional[float] = 0
    discount: Optional[float] = 0
    payment_amount: Optional[float] = 0
    promocode: Optional[str] = None
    qr_token: Optional[str] = None
    qr_signature: Optional[str] = None
    city_name: Optional[str] = None
    country_code: Optional[str] = None
    club_id: Optional[int] = None
    visible_to_managers: Optional[bool] = True

class TicketResponse(BaseModel):
    id: int
    order_id: str
    customer_name: str
    customer_email: Optional[str]
    customer_phone: Optional[str]
    ticket_type: str
    event_date: Optional[str]
    event_name: Optional[str]
    price: float
    promocode: Optional[str]  # ДОБАВЛЕНО: промокод
    status: str
    scan_count: int
    first_scan_at: Optional[datetime]
    qr_token: Optional[str]
    qr_signature: Optional[str]
    created_at: datetime
    city_name: Optional[str]
    country_code: Optional[str]
    club_id: Optional[int]
    visible_to_managers: Optional[bool]
    
    class Config:
        from_attributes = True

class TicketListResponse(BaseModel):
    tickets: List[TicketResponse]
    total: int
    bought: int
    entered: int
    pending: int

class VerifyRequest(BaseModel):
    qr_data: str
    scanner_id: Optional[str] = "default"

class VerifyResponse(BaseModel):
    status: str
    message: str
    data: Optional[dict] = None
    used_at: Optional[str] = None

class StatsResponse(BaseModel):
    total_tickets: int
    entered: int
    pending: int
    cancelled: int
    duplicate_attempts: int
    invalid_attempts: int

class HistoryItem(BaseModel):
    id: int
    order_id: str
    customer_name: str
    ticket_type: str
    event_date: Optional[str]
    status: str
    scan_time: Optional[datetime]
    price: float
    
    class Config:
        from_attributes = True

class HistoryResponse(BaseModel):
    items: List[HistoryItem]
    stats: dict
