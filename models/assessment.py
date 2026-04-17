from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from .enums import ConditionGrade, ConditionGradeColumn


class AssessmentReport(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    candidate_id: UUID = Field(foreign_key="productcandidate.id")
    condition_grade: ConditionGrade = Field(
        sa_column=Column(ConditionGradeColumn(), nullable=False)
    )
    condition_score: float
    defects: list[dict] = Field(default=[], sa_column=Column(JSON, default=[]))
    description_match: float
    risk_flags: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    accessories_confirmed: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    accessories_missing: list[str] = Field(default=[], sa_column=Column(JSON, default=[]))
    reference_match: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    summary: str
    model_used: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
