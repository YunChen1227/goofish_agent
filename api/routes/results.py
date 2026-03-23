from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from sqlmodel import select

from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.negotiation import NegotiationRecord
from goofish_agent.schemas.candidate import CandidateResponse
from goofish_agent.storage.database import get_session

router = APIRouter()


@router.get("/{task_id}/candidates", response_model=list[CandidateResponse])
async def get_candidates(task_id: UUID) -> list[ProductCandidate]:
    with get_session() as session:
        candidates = session.exec(
            select(ProductCandidate).where(ProductCandidate.task_id == task_id)
        ).all()
        return list(candidates)


@router.get("/{task_id}/negotiations")
async def get_negotiations(task_id: UUID) -> list[dict]:
    with get_session() as session:
        candidates = session.exec(
            select(ProductCandidate).where(ProductCandidate.task_id == task_id)
        ).all()
        candidate_ids = [c.id for c in candidates]
        if not candidate_ids:
            return []

        convs = session.exec(
            select(SellerConversation).where(
                SellerConversation.candidate_id.in_(candidate_ids)  # type: ignore[union-attr]
            )
        ).all()
        conv_ids = [c.id for c in convs]
        if not conv_ids:
            return []

        records = session.exec(
            select(NegotiationRecord).where(
                NegotiationRecord.conversation_id.in_(conv_ids)  # type: ignore[union-attr]
            )
        ).all()
        return [
            {
                "id": str(r.id),
                "status": r.status.value,
                "agreed_price": r.agreed_price,
                "round_count": r.round_count,
            }
            for r in records
        ]
