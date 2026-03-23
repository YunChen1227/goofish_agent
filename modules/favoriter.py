from __future__ import annotations

from loguru import logger
from sqlmodel import Session

from goofish_agent.config.settings import get_settings
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.enums import CandidateStatus
from goofish_agent.models.task import Task
from goofish_agent.platform.client import GoofishClient


class Favoriter:
    """Phase 3: Favorite passed candidates, compute composite score, rank."""

    def __init__(self, client: GoofishClient, session: Session) -> None:
        self._client = client
        self._session = session

    async def execute(
        self,
        candidates: list[ProductCandidate],
        reports: list[AssessmentReport],
        task: Task,
    ) -> list[ProductCandidate]:
        logger.info("Phase 3: 收藏与排序")

        report_map = {r.candidate_id: r for r in reports}
        passed = [c for c in candidates if c.status != CandidateStatus.REJECTED]

        for c in passed:
            if await self._client.add_to_favorites(c.platform_product_id):
                c.status = CandidateStatus.FAVORITED

        settings = get_settings()
        has_ref = bool(task.reference_images)

        for c in passed:
            report = report_map.get(c.id)
            if not report:
                continue

            condition_s = report.condition_score / 10.0
            price_range = max(task.max_price - task.target_price, 1.0)
            price_s = max(0.0, 1 - (c.price - task.target_price) / price_range)
            credit_s = min((c.seller_credit or 0) / 1000.0, 1.0)
            desc_s = report.description_match / 10.0

            if has_ref:
                img_s = (report.reference_match or {}).get("score", 0.0)
                c.initial_score = (
                    settings.w_condition_ref * condition_s
                    + settings.w_price_ref * price_s
                    + settings.w_credit_ref * credit_s
                    + settings.w_desc_match_ref * desc_s
                    + settings.w_image_match * img_s
                )
            else:
                c.initial_score = (
                    settings.w_condition * condition_s
                    + settings.w_price * price_s
                    + settings.w_credit * credit_s
                    + settings.w_desc_match * desc_s
                )

        passed.sort(key=lambda x: x.initial_score, reverse=True)
        self._session.commit()

        top_n = passed[: task.max_negotiate_count]
        logger.info(f"Phase 3 完成: {len(top_n)} 个商品进入沟通阶段")
        return top_n
