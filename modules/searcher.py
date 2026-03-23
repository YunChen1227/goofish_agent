from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.task import Task
from goofish_agent.platform.client import GoofishClient
from goofish_agent.platform.parsers.detail_parser import ProductDetail


class Searcher:
    """Phase 1: Search, filter, optional image matching, and initial scoring."""

    def __init__(self, client: GoofishClient, vlm: VLMClient, session: Session) -> None:
        self._client = client
        self._vlm = vlm
        self._session = session

    async def execute(self, task: Task) -> list[ProductCandidate]:
        logger.info(f"Phase 1: 搜索 '{task.keywords}'")

        briefs = await self._client.search(task.keywords, self._build_filters(task))

        candidates: list[ProductCandidate] = []
        for brief in briefs[: task.max_candidates]:
            detail = await self._client.get_product_detail(brief.product_id)
            if not detail:
                continue
            if not self._passes_filters(detail, task):
                continue
            candidates.append(self._to_candidate(detail, task.id))

        if task.reference_images:
            candidates = await self._apply_image_matching(candidates, task)

        candidates = self._score_and_sort(candidates, task)

        for c in candidates:
            self._session.add(c)
        self._session.commit()

        logger.info(f"Phase 1 完成: {len(candidates)} 个候选商品")
        return candidates

    def _build_filters(self, task: Task) -> dict:
        return {
            "min_price": task.min_price,
            "max_price": task.max_price,
            "location": task.location,
        }

    def _passes_filters(self, detail: ProductDetail, task: Task) -> bool:
        if not (task.min_price <= detail.price <= task.max_price):
            return False
        desc_lower = (detail.title + detail.description).lower()
        if any(kw.lower() in desc_lower for kw in task.exclude_keywords):
            return False
        if task.seller_min_credit and detail.seller_credit:
            if detail.seller_credit < task.seller_min_credit:
                return False
        return True

    async def _apply_image_matching(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[ProductCandidate]:
        matched: list[ProductCandidate] = []
        for c in candidates:
            if c.images:
                score = await self._vlm.match_images(c.images[:3], task.reference_images)
                c.image_match_score = score
                if score >= task.image_match_threshold:
                    matched.append(c)
                else:
                    logger.debug(f"图片匹配度 {score:.2f} 低于阈值, 跳过: {c.title}")
            else:
                matched.append(c)
        return matched

    def _score_and_sort(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[ProductCandidate]:
        price_range = task.max_price - task.target_price
        for c in candidates:
            price_s = max(0.0, 1 - (c.price - task.target_price) / price_range) if price_range > 0 else 1.0
            credit_s = min((c.seller_credit or 0) / 1000.0, 1.0)
            if c.image_match_score is not None:
                c.initial_score = 0.3 * price_s + 0.2 * credit_s + 0.3 * c.image_match_score + 0.2
            else:
                c.initial_score = 0.4 * price_s + 0.3 * credit_s + 0.3
        candidates.sort(key=lambda x: x.initial_score, reverse=True)
        return candidates

    @staticmethod
    def _to_candidate(detail: ProductDetail, task_id) -> ProductCandidate:
        return ProductCandidate(
            id=uuid4(),
            task_id=task_id,
            platform_product_id=detail.product_id,
            title=detail.title,
            description=detail.description,
            price=detail.price,
            seller_id=detail.seller_id,
            seller_name=detail.seller_name,
            seller_credit=detail.seller_credit,
            images=detail.images,
            video_url=detail.video_url,
            product_url=detail.product_url,
            created_at=datetime.now(timezone.utc),
        )
