from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    type: str
    message: str
    timestamp: datetime
