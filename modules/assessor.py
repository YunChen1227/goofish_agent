from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger
from sqlmodel import Session, select

from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.goofish_platform.exceptions import (
    BrowserClosedByUserError,
    is_playwright_target_closed_error,
)
from goofish_agent.hooks.image_acquisition_hook import ImageAcquisitionHook
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.enums import CandidateStatus, ConditionGrade
from goofish_agent.models.task import Task
from goofish_agent.storage.media_store import MediaStore


class Assessor:
    """Phase 2: Condition assessment via multi-modal VLM.

    Compared to the original linear implementation, this version:

    * **Concurrent execution** — candidate assessments run in parallel, bounded
      by :attr:`MAX_CONCURRENCY` so the VLM / CDN are not stampeded.
    * **Per-candidate breakpoint** — existing ``AssessmentReport`` rows are
      detected up-front so resumed runs only assess the unfinished candidates,
      and each newly-generated report is committed under a write-lock the
      moment it completes (so a crash mid-batch keeps the partial progress).
    * **Browser-closed propagation** — if the user closes the Playwright
      window, the wrapped exception (``BrowserClosedByUserError`` /
      ``TargetClosedError``) bubbles out of :meth:`execute`, cancelling the
      remaining concurrent assessments and letting :class:`TaskManager`
      terminate the task.
    """

    # How many candidate assessments to run in parallel.  VLM calls are the
    # slow part; keep this small enough that we don't overload the API quota.
    MAX_CONCURRENCY = 3

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
        # Serializes DB writes and *any* use of the single Playwright page
        # across concurrent assessment coroutines.
        self._write_lock = asyncio.Lock()
        self._page_lock = asyncio.Lock()

    async def execute(
        self, candidates: list[ProductCandidate], task: Task
    ) -> list[AssessmentReport]:
        logger.info(
            f"Phase 2: 品相鉴定 {len(candidates)} 个商品 | "
            f"最低品相要求: {task.condition_requirement} "
            f"(分数≥{task.condition_requirement.score}) | "
            f"并发度: {self.MAX_CONCURRENCY}"
        )

        # Load existing reports so we can honor per-candidate breakpoints.
        existing_reports = self._load_existing_reports(candidates)
        if existing_reports:
            logger.info(
                f"[断点续跑] 检测到 {len(existing_reports)} 个商品已有评估报告，"
                f"将跳过重新评估"
            )

        todo = [c for c in candidates if c.id not in existing_reports]
        all_reports: list[AssessmentReport] = list(existing_reports.values())

        if not todo:
            logger.info("Phase 2: 所有候选均已评估，无新增任务")
            return all_reports

        logger.info(
            f"[品相鉴定] 待评估 {len(todo)} 个商品 | 已有 {len(existing_reports)} 个缓存报告"
        )

        semaphore = asyncio.Semaphore(self.MAX_CONCURRENCY)
        threshold = task.condition_requirement.score

        async def _bounded_assess(idx: int, candidate: ProductCandidate) -> AssessmentReport | None:
            async with semaphore:
                logger.info(
                    f"[品相鉴定 {idx}/{len(todo)}] 开始分析商品: {candidate.title} | "
                    f"价格: ¥{candidate.price} | 卖家: {candidate.seller_name} | "
                    f"图片数: {len(candidate.images)}"
                )
                report = await self._assess_one(candidate, task)
                if report is None:
                    logger.warning(
                        f"[品相鉴定 {idx}/{len(todo)}] 评估失败，跳过该商品"
                    )
                    return None

                passed = report.condition_score >= threshold
                logger.info(
                    f"[品相鉴定 {idx}/{len(todo)}] 分析结果汇总:\n"
                    f"  商品: {candidate.title}\n"
                    f"  品相等级: {report.condition_grade.name}\n"
                    f"  品相评分: {report.condition_score}/10 (要求≥{threshold})\n"
                    f"  描述一致性: {report.description_match}/10\n"
                    f"  瑕疵数量: {len(report.defects)}\n"
                    f"  风险标记: {report.risk_flags if report.risk_flags else '无'}\n"
                    f"  评估总结: {report.summary}\n"
                    f"  >>> 判定: {'✓ 通过' if passed else '✗ 不通过'}"
                )

                # Atomic per-report commit: protects session from concurrent writes
                # and ensures a crash keeps finished reports durable.
                async with self._write_lock:
                    self._session.add(report)
                    if not passed:
                        candidate.status = CandidateStatus.REJECTED
                        logger.info(
                            f"[品相鉴定] 商品被淘汰: {candidate.title} | "
                            f"评分 {report.condition_score} < 要求 {threshold}"
                        )
                    self._session.commit()
                return report

        tasks = [
            asyncio.create_task(_bounded_assess(i, c)) for i, c in enumerate(todo, 1)
        ]
        try:
            new_reports = await asyncio.gather(*tasks)
        except BrowserClosedByUserError:
            # Make sure no stragglers keep running after we re-raise.
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise
        except Exception as e:
            if is_playwright_target_closed_error(e):
                for t in tasks:
                    if not t.done():
                        t.cancel()
                raise BrowserClosedByUserError(
                    "用户关闭了 Playwright 浏览器页面，任务已终止"
                ) from e
            raise

        all_reports.extend(r for r in new_reports if r is not None)

        passed_reports = [
            r for r in all_reports if r.condition_score >= threshold
        ]
        logger.info(
            f"{'='*60}\n"
            f"Phase 2 完成: {len(passed_reports)}/{len(candidates)} 通过品相鉴定"
        )
        return all_reports

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_existing_reports(
        self, candidates: list[ProductCandidate]
    ) -> dict:
        """Return ``{candidate_id: AssessmentReport}`` for any candidate that
        already has a report persisted in the database."""
        if not candidates:
            return {}
        ids = [c.id for c in candidates]
        rows = self._session.exec(
            select(AssessmentReport).where(AssessmentReport.candidate_id.in_(ids))
        ).all()
        return {r.candidate_id: r for r in rows}

    async def _assess_one(
        self, candidate: ProductCandidate, task: Task
    ) -> AssessmentReport | None:
        try:
            images: list[str] = list(candidate.images[:6])
            if self._image_hook is not None and candidate.images:
                try:
                    # The single Playwright ``page`` cannot be driven in parallel;
                    # serialize the hook invocation (it falls back to pure-HTTP
                    # strategies which are themselves safe to run concurrently
                    # inside, but the method as a whole still touches a shared
                    # page object for screenshot fallback).
                    async with self._page_lock:
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
                except BrowserClosedByUserError:
                    raise
                except Exception as e:
                    if is_playwright_target_closed_error(e):
                        raise BrowserClosedByUserError(
                            "用户关闭了 Playwright 浏览器页面，任务已终止"
                        ) from e
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
        except BrowserClosedByUserError:
            # Let this bubble out of the gather so the whole task terminates.
            raise
        except Exception as e:
            if is_playwright_target_closed_error(e):
                raise BrowserClosedByUserError(
                    "用户关闭了 Playwright 浏览器页面，任务已终止"
                ) from e
            logger.error(f"评估失败 {candidate.title}: {e}")
            return None
