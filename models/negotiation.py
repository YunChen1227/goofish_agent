from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from .enums import NegotiationStatus


class NegotiationRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    conversation_id: UUID = Field(foreign_key="sellerconversation.id")
    initial_price: float
    target_price: float
    current_offer: Optional[float] = None
    seller_counter: Optional[float] = None
    round_count: int = 0
    agreed_price: Optional[float] = None
    market_reference: dict = Field(default={}, sa_column=Column(JSON, default={}))
    status: NegotiationStatus = NegotiationStatus.IN_PROGRESS
    history: list[dict] = Field(default=[], sa_column=Column(JSON, default=[]))
