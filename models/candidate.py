from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from .enums import CandidateStatus


class ProductCandidate(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_id: UUID = Field(foreign_key="task.id")
    platform_product_id: str
    title: str
    description: str
    price: float
    seller_id: str
    seller_name: str
    seller_credit: Optional[int] = None
    images: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    video_url: Optional[str] = None
    product_url: str
    initial_score: float = 0
    image_match_score: Optional[float] = None
    status: CandidateStatus = CandidateStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
