from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.enums import CandidateStatus, ConditionGrade
from goofish_agent.models.task import Task
from goofish_agent.storage.media_store import MediaStore


class Assessor:
    """Phase 2: Condition assessment via multi-modal VLM."""

    def __init__(self, vlm: VLMClient, media_store: MediaStore, session: Session) -> None:
        self._vlm = vlm
        self._media = media_store
        self._session = session

    async def execute(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[AssessmentReport]:
        logger.info(f"Phase 2: 品相鉴定 {len(candidates)} 个商品")
        reports: list[AssessmentReport] = []

        for candidate in candidates:
            report = await self._assess_one(candidate, task)
            if not report:
                continue
            reports.append(report)
            if report.condition_score < task.condition_requirement.score:
                candidate.status = CandidateStatus.REJECTED
                logger.info(
                    f"品相不达标: {candidate.title} "
                    f"({report.condition_score} < {task.condition_requirement.score})"
                )
            self._session.add(report)

        self._session.commit()
        passed = [r for r in reports if r.condition_score >= task.condition_requirement.score]
        logger.info(f"Phase 2 完成: {len(passed)}/{len(candidates)} 通过品相鉴定")
        return reports

    async def _assess_one(
        self, candidate: ProductCandidate, task: Task
    ) -> AssessmentReport | None:
        try:
            images = candidate.images[:6]
            ref_images = task.reference_images if task.reference_images else None

            result = await self._vlm.assess_product(
                images=images,
                description=f"{candidate.title}\n{candidate.description}",
                reference_images=ref_images,
            )

            return AssessmentReport(
                id=uuid4(),
                candidate_id=candidate.id,
                condition_grade=ConditionGrade[result.get("condition_grade", "FAIR")],
                condition_score=float(result.get("condition_score", 5)),
                defects=result.get("defects", []),
                description_match=float(result.get("description_match", 5)),
                risk_flags=result.get("risk_flags", []),
                accessories_confirmed=result.get("accessories_confirmed", []),
                accessories_missing=result.get("accessories_missing", []),
                reference_match=result.get("reference_match"),
                summary=result.get("summary", ""),
                model_used=self._vlm._model,
                created_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"评估失败 {candidate.title}: {e}")
            return None
