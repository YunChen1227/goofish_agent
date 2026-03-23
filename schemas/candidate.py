from uuid import UUID

from pydantic import BaseModel


class CandidateResponse(BaseModel):
    id: UUID
    title: str
    price: float
    seller_name: str
    initial_score: float
    image_match_score: float | None
    status: str
    product_url: str
    images: list[str] = []

    model_config = {"from_attributes": True}
