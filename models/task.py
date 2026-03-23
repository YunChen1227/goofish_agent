from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Integer
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from .enums import (
    ConditionGrade,
    NotificationChannel,
    TaskPhase,
    TaskStatus,
)


class Task(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID
    keywords: str
    min_price: float = 0
    max_price: float
    target_price: float
    condition_requirement: ConditionGrade = Field(
        sa_column=Column(Integer, nullable=False)
    )
    location: Optional[str] = None
    exclude_keywords: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    max_candidates: int = 20
    max_negotiate_count: int = 5
    negotiate_rounds_limit: int = 10
    seller_min_credit: Optional[int] = None
    prefer_verified: bool = False
    reference_images: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    image_match_threshold: float = 0.6
    custom_instructions: Optional[str] = None
    notification_channel: NotificationChannel = NotificationChannel.IN_APP
    status: TaskStatus = TaskStatus.PENDING
    current_phase: Optional[TaskPhase] = None
    result_summary: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
