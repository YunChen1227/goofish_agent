from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    platform: str
    keywords: str
    max_price: float
    target_price: float
    condition_requirement: str = "LIKE_NEW"
    min_price: float = 0
    location: str | None = None
    exclude_keywords: list[str] = []
    max_candidates: int = 20
    max_negotiate_count: int = 5
    negotiate_rounds_limit: int = 10
    seller_min_credit: int | None = None
    prefer_verified: bool = False
    reference_images: list[str] = []
    image_match_threshold: float = 0.6
    damage_pattern_description: str | None = None
    damage_example_images: list[str] = []
    buyer_todo_list: list[dict] | str = Field(default_factory=list)
    custom_instructions: str | None = None
    notification_channel: str = "IN_APP"


class TaskResponse(BaseModel):
    id: UUID
    platform: str
    keywords: str
    status: str
    current_phase: str | None
    max_price: float
    target_price: float
    created_at: datetime
    updated_at: datetime
    result_summary: dict | None = None

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    tasks: list[TaskResponse]
    total: int
