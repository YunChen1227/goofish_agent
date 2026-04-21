from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from loguru import logger
from sqlmodel import Session, select

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.config.logging import disable_task_log, enable_task_log
from goofish_agent.ai.market_analyzer import MarketAnalyzer
from goofish_agent.ai.vlm_client import VLMClient
from goofish_agent.core.state_machine import PHASE_ORDER, StateMachine
from goofish_agent.hooks.image_acquisition_hook import ImageAcquisitionHook
from goofish_agent.skills.image_acquisition_skill import ImageAcquisitionSkill
from goofish_agent.models.assessment import AssessmentReport
from goofish_agent.models.candidate import ProductCandidate
from goofish_agent.models.conversation import SellerConversation
from goofish_agent.models.enums import (
    CandidateStatus,
    ChatStatus,
    NegotiationStatus,
    PlatformType,
    TaskPhase,
    TaskStatus,
)
from goofish_agent.models.negotiation import NegotiationRecord
from goofish_agent.models.task import Task
from goofish_agent.goofish_platform.exceptions import (
    BrowserClosedByUserError,
    is_playwright_target_closed_error,
)
from goofish_agent.modules.assessor import Assessor
from goofish_agent.modules.chatter import Chatter
from goofish_agent.modules.favoriter import Favoriter
from goofish_agent.modules.negotiator import Negotiator
from goofish_agent.modules.notifier import Notifier
from goofish_agent.modules.searcher import Searcher
from goofish_agent.platform import PlatformClient, create_platform_client
from goofish_agent.storage.database import get_session
from goofish_agent.storage.media_store import MediaStore


def _phase_index(phase: Optional[TaskPhase]) -> int:
    """Index of *phase* in :data:`PHASE_ORDER`; ``-1`` for ``None``/unknown."""
    if phase is None:
        return -1
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return -1


class TaskManager:
    def __init__(self, platform: PlatformType) -> None:
        self._platform = platform
        self._client: PlatformClient | None = None
        self._vlm: VLMClient | None = None
        self._llm: LLMClient | None = None
        self._market: MarketAnalyzer | None = None
        self._media: MediaStore | None = None

    async def initialize(self) -> None:
        self._client = create_platform_client(self._platform)
        self._vlm = VLMClient()
        self._llm = LLMClient()
        self._market = MarketAnalyzer(self._client)
        self._media = MediaStore()
        await self._client.start()

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()

    # ------------------------------------------------------------------
    # Public task lifecycle
    # ------------------------------------------------------------------

    async def run_task(self, task_id: UUID) -> None:
        """Execute a buying task (fresh or resumed) through remaining phases.

        All log records emitted while this coroutine is active are tagged with
        ``task_id`` (via ``logger.contextualize``) and routed to a dedicated
        per-task sink ``logs/tasks/{task_id}.log``. The UI streams from that
        file so each task sees only its own logs; deleting the task removes
        the file entirely (see ``api.routes.tasks``).
        """
        sink_id = enable_task_log(task_id)
        try:
            with logger.contextualize(task_id=str(task_id)):
                await self._run_task_inner(task_id)
        finally:
            disable_task_log(sink_id)

    async def _run_task_inner(self, task_id: UUID) -> None:
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            is_resume = task.current_phase is not None and task.status in (
                TaskStatus.PAUSED,
                TaskStatus.ERROR,
            )

            StateMachine.transition(task, TaskStatus.RUNNING)
            session.commit()

            if is_resume:
                logger.info(
                    f"[断点续跑] Task {task_id} 从阶段 "
                    f"{task.current_phase.value if task.current_phase else 'SEARCHING'} 继续"
                )

            try:
                await self._execute_phases(task, session)
            except BrowserClosedByUserError as e:
                logger.error(f"Task {task_id} 已终止：用户关闭了浏览器 — {e}")
                if StateMachine.can_transition(task.status, TaskStatus.ERROR):
                    StateMachine.transition(task, TaskStatus.ERROR)
                    session.commit()
                raise
            except Exception as e:
                if is_playwright_target_closed_error(e):
                    logger.error(
                        f"Task {task_id} 已终止：Playwright 页面/浏览器已关闭 — {e}"
                    )
                    if StateMachine.can_transition(task.status, TaskStatus.ERROR):
                        StateMachine.transition(task, TaskStatus.ERROR)
                        session.commit()
                    raise BrowserClosedByUserError(
                        "用户关闭了 Playwright 浏览器页面，任务已终止"
                    ) from e
                logger.error(f"Task {task_id} error: {e}")
                if StateMachine.can_transition(task.status, TaskStatus.ERROR):
                    StateMachine.transition(task, TaskStatus.ERROR)
                    session.commit()
                raise

    async def pause_task(self, task_id: UUID) -> None:
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            StateMachine.transition(task, TaskStatus.PAUSED)
            session.commit()

    async def resume_task(self, task_id: UUID) -> None:
        """Resume a previously paused / errored task from its last phase.

        Unlike a fresh run, we *do not* clear ``current_phase`` so that the
        phase-level resume logic in :meth:`_execute_phases` can skip the
        phases that already completed.  Per-phase idempotency (e.g. cached
        briefs, already-persisted candidates / reports) handles mid-phase
        restarts.
        """
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            if task.status not in (TaskStatus.PAUSED, TaskStatus.ERROR):
                raise ValueError(
                    f"Task status {task.status.value} cannot be resumed"
                )
        await self.run_task(task_id)

    async def cancel_task(self, task_id: UUID) -> None:
        with get_session() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            StateMachine.transition(task, TaskStatus.CANCELLED)
            session.commit()

    # ------------------------------------------------------------------
    # Internal phase execution
    # ------------------------------------------------------------------

    def _set_phase(self, task: Task, phase: TaskPhase, session: Session) -> None:
        task.current_phase = phase
        task.updated_at = datetime.now(timezone.utc)
        session.commit()

    def _should_skip_phase(self, task: Task, phase: TaskPhase) -> bool:
        """True if *phase* already finished previously (strictly past it)."""
        return _phase_index(task.current_phase) > _phase_index(phase)

    @staticmethod
    def _load_candidates(task: Task, session: Session) -> list[ProductCandidate]:
        return list(
            session.exec(
                select(ProductCandidate).where(ProductCandidate.task_id == task.id)
            ).all()
        )

    @staticmethod
    def _load_reports(
        candidates: list[ProductCandidate], session: Session
    ) -> list[AssessmentReport]:
        if not candidates:
            return []
        ids = [c.id for c in candidates]
        return list(
            session.exec(
                select(AssessmentReport).where(AssessmentReport.candidate_id.in_(ids))
            ).all()
        )

    @staticmethod
    def _load_conversations(
        candidates: list[ProductCandidate], session: Session
    ) -> list[SellerConversation]:
        if not candidates:
            return []
        ids = [c.id for c in candidates]
        return list(
            session.exec(
                select(SellerConversation).where(
                    SellerConversation.candidate_id.in_(ids)
                )
            ).all()
        )

    @staticmethod
    def _load_negotiations(
        conversations: list[SellerConversation], session: Session
    ) -> list[NegotiationRecord]:
        if not conversations:
            return []
        ids = [c.id for c in conversations]
        return list(
            session.exec(
                select(NegotiationRecord).where(
                    NegotiationRecord.conversation_id.in_(ids)
                )
            ).all()
        )

    async def _execute_phases(self, task: Task, session: Session) -> None:
        assert self._client and self._vlm and self._llm and self._market and self._media
        notifier = Notifier(session)

        # --- Phase 1: Search -------------------------------------------------
        if self._should_skip_phase(task, TaskPhase.SEARCHING):
            candidates = self._load_candidates(task, session)
            logger.info(
                f"[断点续跑] 跳过 Phase 1（搜索），从数据库加载 {len(candidates)} 个候选商品"
            )
        else:
            self._set_phase(task, TaskPhase.SEARCHING, session)
            await notifier.notify_progress(task, "搜索", "开始搜索商品...")
            searcher = Searcher(self._client, self._vlm, self._llm, session)
            candidates = await searcher.execute(task)
            if not candidates:
                StateMachine.transition(task, TaskStatus.FAILED)
                await notifier.notify_failure(task, {"filtered": 0})
                session.commit()
                return

        # --- Phase 2: Assessment (concurrent, breakpoint-friendly) -----------
        if self._should_skip_phase(task, TaskPhase.ASSESSING):
            reports = self._load_reports(candidates, session)
            logger.info(
                f"[断点续跑] 跳过 Phase 2（品相鉴定），从数据库加载 {len(reports)} 份评估报告"
            )
        else:
            self._set_phase(task, TaskPhase.ASSESSING, session)
            await notifier.notify_progress(
                task, "品相鉴定", f"评估 {len(candidates)} 个商品..."
            )

            image_skill = ImageAcquisitionSkill()
            image_hook = ImageAcquisitionHook(skill=image_skill)
            page = getattr(self._client, "_page", None)
            assessor = Assessor(
                self._vlm, self._media, session, image_hook=image_hook, page=page
            )
            reports = await assessor.execute(candidates, task)

        passed = [c for c in candidates if c.status != CandidateStatus.REJECTED]
        if not passed:
            StateMachine.transition(task, TaskStatus.FAILED)
            await notifier.notify_failure(
                task, {"assessment_rejected": len(candidates)}
            )
            session.commit()
            return

        # --- Phase 3: Favorite & Rank ----------------------------------------
        if self._should_skip_phase(task, TaskPhase.FAVORITING):
            # Re-derive top_candidates from DB (already favorited + scored)
            top_candidates = sorted(
                passed, key=lambda x: x.initial_score, reverse=True
            )[: task.max_negotiate_count]
            logger.info(
                f"[断点续跑] 跳过 Phase 3（收藏排序），选取前 {len(top_candidates)} 个进入沟通"
            )
        else:
            self._set_phase(task, TaskPhase.FAVORITING, session)
            favoriter = Favoriter(self._client, session)
            top_candidates = await favoriter.execute(candidates, reports, task)
            await notifier.notify_progress(
                task, "收藏排序", f"{len(top_candidates)} 个商品进入沟通"
            )

        # --- Phase 4: Chat ---------------------------------------------------
        if self._should_skip_phase(task, TaskPhase.CHATTING):
            conversations = self._load_conversations(top_candidates, session)
            logger.info(
                f"[断点续跑] 跳过 Phase 4（卖家沟通），从数据库加载 {len(conversations)} 个对话"
            )
        else:
            self._set_phase(task, TaskPhase.CHATTING, session)
            chatter = Chatter(self._client, self._llm, session)
            conversations = await chatter.execute(top_candidates, task)

        ready_convs = [c for c in conversations if c.chat_status == ChatStatus.READY]
        if not ready_convs:
            StateMachine.transition(task, TaskStatus.FAILED)
            await notifier.notify_failure(
                task, {"chat_failed": len(conversations) - len(ready_convs)}
            )
            session.commit()
            return

        # --- Phase 5: Negotiate ----------------------------------------------
        if self._should_skip_phase(task, TaskPhase.NEGOTIATING):
            records = self._load_negotiations(conversations, session)
            logger.info(
                f"[断点续跑] 跳过 Phase 5（价格谈判），从数据库加载 {len(records)} 条谈判记录"
            )
        else:
            self._set_phase(task, TaskPhase.NEGOTIATING, session)
            negotiator = Negotiator(self._client, self._llm, self._market, session)
            records = await negotiator.execute(conversations, top_candidates, task)

        agreed = [r for r in records if r.status == NegotiationStatus.AGREED]

        # --- Phase 6: Notify -------------------------------------------------
        self._set_phase(task, TaskPhase.NOTIFYING, session)

        if agreed:
            for record in agreed:
                conv = next(
                    (c for c in conversations if c.id == record.conversation_id), None
                )
                candidate = next(
                    (c for c in top_candidates if conv and c.id == conv.candidate_id),
                    None,
                )
                if candidate:
                    await notifier.notify_deal(task, candidate, record)
            StateMachine.transition(task, TaskStatus.COMPLETED)
        else:
            await notifier.notify_failure(
                task, {"negotiation_failed": len(records) - len(agreed)}
            )
            StateMachine.transition(task, TaskStatus.FAILED)

        session.commit()
