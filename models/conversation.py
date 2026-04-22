from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from .enums import ChatStatus


class SellerConversation(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    candidate_id: UUID = Field(foreign_key="productcandidate.id")
    seller_id: str
    platform_conversation_id: Optional[str] = None
    messages: list[dict] = Field(default=[], sa_column=Column(JSON, default=[]))
    chat_status: ChatStatus = ChatStatus.INIT
    seller_attitude: Optional[str] = None
    info_collected: dict = Field(default={}, sa_column=Column(JSON, default={}))
    # TODO 驱动对话的核心状态（items + plan），见 ai/todo_agents.py 的结构说明
    todo_state: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: Optional[datetime] = None
