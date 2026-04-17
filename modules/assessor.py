from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session

from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.hooks.image_acquisition_hook import ImageAcquisitionHook
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.enums import CandidateStatus, ConditionGrade
from goofish_agent.models.task import Task
from goofish_agent.storage.media_store import MediaStore


class Assessor:
    """Phase 2: Condition assessment via multi-modal VLM."""

    def __init__(
        self,
        vlm: VLMClient,
        media_store: MediaStore,
        session: Session,
        image_hook: ImageAcquisitionHook | None = None,
        page: object | None = None,
    ) -> None:
        self._vlm = vlm
        self._media = media_store
        self._session = session
        self._image_hook = image_hook
        self._page = page

    async def execute(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[AssessmentReport]:
        logger.info(
            f"Phase 2: 品相鉴定 {len(candidates)} 个商品 | "
            f"最低品相要求: {task.condition_requirement} (分数≥{task.condition_requirement.score})"
        )
        reports: list[AssessmentReport] = []

        for idx, candidate in enumerate(candidates, 1):
            logger.info(
                f"{'='*60}\n"
                f"[品相鉴定 {idx}/{len(candidates)}] 开始分析商品: {candidate.title}\n"
                f"  价格: ¥{candidate.price} | 卖家: {candidate.seller_name} | "
                f"图片数: {len(candidate.images)}"
            )

            report = await self._assess_one(candidate, task)
            if not report:
                logger.warning(f"[品相鉴定 {idx}/{len(candidates)}] 评估失败，跳过该商品")
                continue

            reports.append(report)
            threshold = task.condition_requirement.score
            passed = report.condition_score >= threshold

            logger.info(
                f"[品相鉴定 {idx}/{len(candidates)}] 分析结果汇总:\n"
                f"  商品: {candidate.title}\n"
                f"  品相等级: {report.condition_grade.name}\n"
                f"  品相评分: {report.condition_score}/10 (要求≥{threshold})\n"
                f"  描述一致性: {report.description_match}/10\n"
                f"  瑕疵数量: {len(report.defects)}\n"
                f"  风险标记: {report.risk_flags if report.risk_flags else '无'}\n"
                f"  配件已确认: {report.accessories_confirmed if report.accessories_confirmed else '无'}\n"
                f"  配件缺失: {report.accessories_missing if report.accessories_missing else '无'}\n"
                f"  评估总结: {report.summary}\n"
                f"  >>> 判定: {'✓ 通过' if passed else '✗ 不通过'}"
            )

            if not passed:
                candidate.status = CandidateStatus.REJECTED
                logger.info(
                    f"[品相鉴定] 商品被淘汰: {candidate.title} | "
                    f"评分 {report.condition_score} < 要求 {threshold}"
                )
            self._session.add(report)

        self._session.commit()
        passed_reports = [r for r in reports if r.condition_score >= task.condition_requirement.score]
        logger.info(
            f"{'='*60}\n"
            f"Phase 2 完成: {len(passed_reports)}/{len(candidates)} 通过品相鉴定"
        )
        return reports

    async def _assess_one(
        self, candidate: ProductCandidate, task: Task
    ) -> AssessmentReport | None:
        try:
            images: list[str] = list(candidate.images[:6])
            if self._image_hook is not None and candidate.images:
                try:
                    acq = await self._image_hook.acquire_for_candidate(
                        candidate=candidate, page=self._page
                    )
                    if acq.base64_images:
                        logger.info(
                            f"[图片获取] {candidate.title} | "
                            f"本地 {len(acq.base64_images)} 张 | "
                            f"失败 {len(acq.failed_urls)} | 策略={acq.methods}"
                        )
                        images = acq.base64_images
                    else:
                        logger.warning(
                            f"[图片获取] 全部策略失败，回退原始 URL: {candidate.title}"
                        )
                except Exception as e:
                    logger.warning(f"[图片获取] 异常，回退原始 URL: {e}")
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
